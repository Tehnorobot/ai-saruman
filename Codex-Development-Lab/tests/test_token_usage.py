"""CLI accounting never double-counts cache, duplicate transports or billing."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import token_usage


class TokenUsageTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.art=self.root/'artifacts';self.box=self.root/'mailbox'
        self.pid='a'*14;self.jid='b'*14;self.serial=0
        for directory in ('requests','running','logs','results'):(self.box/directory).mkdir(parents=True)
        token_usage._CACHE.clear()

    def event(self,input=100,cached=60,output=20,**extra):
        return json.dumps({'type':'turn.completed','usage':{
            'input_tokens':input,'cached_input_tokens':cached,'output_tokens':output,
            'reasoning_output_tokens':10,**extra}})+'\n'

    def attempt(self,role='coder',provider='codex',status='PASS',log=None,live=False):
        self.serial+=1;tid=f'{self.jid}-round{self.serial:02d}-'+f'{self.serial:032x}'
        request={'id':tid,'project_id':self.pid,'job_id':self.jid,'provider':provider,
                 'role':role,'model':'test-model','prompt':'PRIVATE PROMPT SHOULD NOT BE RETURNED'}
        if live:
            (self.box/'running'/f'{tid}.json').write_text(json.dumps(request),encoding='utf-8')
            path=self.box/'logs'/f'{tid}.jsonl'
        else:
            folder=self.art/self.pid/self.jid/f'round-{self.serial:02d}'/'agent-history'/tid
            folder.mkdir(parents=True)
            (folder/'request.json').write_text(json.dumps(request),encoding='utf-8')
            (folder/'meta.json').write_text(json.dumps({'id':tid,'project_id':self.pid,
                'job_id':self.jid,'status':status,'created_at':'2026-10-25T10:00:00+00:00'}),encoding='utf-8')
            path=folder/'events.jsonl'
        path.write_text(self.event() if log is None else log,encoding='utf-8')
        return tid,path,request

    def report(self,boxes=None):
        return token_usage.codex_usage(self.art,boxes or [self.box],self.pid,self.jid)

    def test_full_background_history_keeps_every_turn_with_stable_unique_identity(self):
        tid,path,_=self.attempt(log=self.event()*237)
        report=token_usage.codex_usage(self.art,[self.box],self.pid,self.jid,request_limit=None)
        self.assertEqual(len(report['requests']),237)
        self.assertEqual(len({r['request_id'] for r in report['requests']}),237)
        self.assertEqual(report['totals']['requests'],237)
        self.assertEqual(sum(r['input_tokens_total'] for r in report['requests']),23700)
        before=[r['request_id'] for r in report['requests']]
        with path.open('a') as f:f.write(self.event())
        updated=token_usage.codex_usage(self.art,[self.box],self.pid,self.jid,request_limit=None)
        self.assertEqual(len(updated['requests']),238)
        self.assertEqual([r['request_id'] for r in updated['requests']][1:],before)
        self.assertFalse(updated['truncated'])

    def test_cache_is_subset_of_input_and_reasoning_subset_of_output(self):
        self.attempt(log=self.event(cache_write_input_tokens=10))
        totals=self.report()['totals']
        self.assertEqual(totals['input_tokens'],40)
        self.assertEqual(totals['input_tokens_total'],100)
        self.assertEqual(totals['output_tokens'],20)
        self.assertEqual(totals['total_tokens'],120)
        self.assertEqual(totals['saved_input_tokens'],60)
        self.assertEqual(totals['cache_hit_percent'],60)
        self.assertIsNone(totals['estimated_rub'])

    def test_archive_mailboxes_and_root_copies_do_not_duplicate_same_attempt(self):
        tid,path,request=self.attempt()
        (self.box/'running'/f'{tid}.json').write_text(json.dumps(request))
        (self.box/'logs'/f'{tid}.jsonl').write_text(self.event(input=1000,cached=100))
        (path.parents[2]/'codex.jsonl').write_text(self.event(input=10000))
        report=self.report({'coder':self.box,'planner':self.box})
        self.assertEqual(report['totals']['requests'],1)
        self.assertEqual(report['totals']['input_tokens_total'],100)

    def test_multiple_completed_turns_count_but_other_events_do_not(self):
        self.attempt(log=self.event()+self.event(input=200,cached=50)+
            json.dumps({'type':'usage.updated','usage':{'input_tokens':999999}})+'\n')
        report=self.report()
        self.assertEqual(report['totals']['requests'],2)
        self.assertEqual(report['totals']['input_tokens_total'],300)
        self.assertEqual(report['totals']['cache_read_input_tokens'],110)

    def test_roles_and_utc_daily(self):
        self.attempt(role='planner');self.attempt(role='coder')
        report=self.report()
        self.assertEqual({r['role'] for r in report['roles']},{'planner','coder'})
        self.assertEqual(report['daily'][0]['day'],'2026-10-25')
        self.assertEqual(report['daily'][0]['total_tokens'],240)

    def test_active_and_failed_attempts_without_final_usage_are_unknown(self):
        self.attempt(log='',live=True);self.attempt(status='FAIL',log='{"type":"turn.failed"}\n')
        report=self.report()
        self.assertEqual(report['missing_usage_attempts'],2)
        self.assertFalse(report['available'])

    def test_live_growth_is_counted_once_and_cached(self):
        tid,path,request=self.attempt(live=True)
        self.assertEqual(self.report()['totals']['requests'],1)
        with path.open('a') as out:out.write(self.event(input=50,cached=10))
        self.assertEqual(self.report()['totals']['requests'],2)
        self.assertEqual(self.report()['totals']['requests'],2)
        self.assertEqual(self.report()['totals']['input_tokens_total'],150)

    def test_partial_live_json_is_not_counted_until_completed(self):
        event=self.event();tid,path,_=self.attempt(live=True,log=event[:-4])
        self.assertFalse(self.report()['available'])
        with path.open('a') as out:out.write(event[-4:])
        self.assertEqual(self.report()['totals']['requests'],1)

    def test_api_attempts_are_excluded_even_if_log_contains_codex_shaped_usage(self):
        self.attempt(provider='dslab');self.attempt(provider='litellm')
        self.assertEqual(self.report()['totals']['requests'],0)
        self.assertEqual(self.report()['missing_usage_attempts'],0)

    def test_log_replacement_invalidates_cached_counters(self):
        tid,path,_=self.attempt();self.report()
        replacement=path.with_suffix('.new');replacement.write_text(self.event(input=200,cached=50))
        replacement.replace(path)
        self.assertEqual(self.report()['totals']['input_tokens_total'],200)

    def test_same_inode_rewrite_to_larger_file_does_not_reuse_old_counts(self):
        tid,path,_=self.attempt();self.report()
        path.write_text(self.event(input=200,cached=50)+self.event(input=300,cached=50))
        report=self.report()
        self.assertEqual(report['totals']['input_tokens_total'],500)
        self.assertEqual(report['totals']['requests'],2)

    def test_malformed_only_attempt_is_visible_without_inventing_usage(self):
        self.attempt(log=self.event(cached=1000))
        report=self.report()
        self.assertFalse(report['available'])
        self.assertEqual(report['malformed_usage_events'],1)

    def test_malformed_usage_never_invents_counts_and_no_private_text_is_returned(self):
        self.attempt(log=self.event(input=True)+self.event(cached=101)+self.event()+
            '{"type":"item.completed","api_key":"SECRET"}\n')
        report=self.report()
        self.assertEqual(report['malformed_usage_events'],2)
        self.assertEqual(report['totals']['requests'],1)
        self.assertNotIn('SECRET',json.dumps(report))
        self.assertNotIn('PRIVATE PROMPT',json.dumps(report))

    def test_symlink_events_are_not_followed(self):
        tid,path,_=self.attempt();outside=self.root/'outside';outside.write_text(self.event(input=999,cached=1))
        path.unlink()
        try:path.symlink_to(outside)
        except OSError:self.skipTest('Symlink creation unavailable')
        self.assertFalse(self.report()['available'])

    def test_live_result_can_move_to_archive_without_double_counting(self):
        tid,path,request=self.attempt(live=True);self.report()
        folder=self.art/self.pid/self.jid/'round-01'/'agent-history'/tid;folder.mkdir(parents=True)
        (folder/'request.json').write_text(json.dumps(request))
        (folder/'meta.json').write_text(json.dumps({'id':tid,'project_id':self.pid,'job_id':self.jid,'status':'PASS'}))
        (folder/'events.jsonl').write_text(path.read_text())
        path.unlink();(self.box/'running'/f'{tid}.json').unlink()
        self.assertEqual(self.report()['totals']['requests'],1)

    def test_mixed_gateway_preserves_cache_and_marks_combined_money_unknown(self):
        self.attempt();cli=self.report()
        api={'available':True,'totals':{'requests':2,'input_tokens':80,'output_tokens':10,
             'cache_creation_input_tokens':5,'cache_read_input_tokens':15,'estimated_rub':2.5},
             'roles':[{'role':'coder','requests':2,'input_tokens':80,'output_tokens':10,
              'cache_creation_input_tokens':5,'cache_read_input_tokens':15,'estimated_rub':2.5}], 'requests':[]}
        merged=token_usage.merge_usage(api,cli)
        self.assertEqual(merged['totals']['input_tokens_total'],200)
        self.assertEqual(merged['totals']['total_tokens'],230)
        self.assertEqual(merged['totals']['saved_input_tokens'],75)
        self.assertIsNone(merged['totals']['estimated_rub'])
        self.assertEqual(merged['api_totals']['estimated_rub'],2.5)
        self.assertEqual(merged['totals']['api_requests'],2)
        self.assertEqual(merged['totals']['codex_completed_turns'],1)
        self.assertTrue(merged['api_available'])

    def test_gateway_failure_does_not_hide_available_codex_usage(self):
        self.attempt()
        report=token_usage.merge_usage({'available':False,'detail':'gateway offline'},self.report())
        self.assertTrue(report['available']);self.assertFalse(report['api_available'])
        self.assertTrue(report['codex_usage_available'])

    def test_mixed_epoch_and_iso_history_has_stable_utc_order_without_mutating_dates(self):
        self.attempt();cli=self.report()
        cli['requests']=[{'task_id':'cli','started_at':'2026-10-25T11:00:00+02:00'}]
        api={'available':True,'totals':{'requests':4,'estimated_rub':2.5},'roles':[],
             'requests':[{'task_id':'old','started_at':0.0},
                         {'task_id':'latest','started_at':1792922400.0},
                         {'task_id':'missing','started_at':None},
                         {'task_id':'bad','started_at':'not a date'}]}
        original=json.dumps(api)
        report=token_usage.merge_usage(api,cli)
        self.assertEqual([r['task_id'] for r in report['requests']],
                         ['latest','cli','old','missing','bad'])
        self.assertEqual(report['requests'][0]['started_at'],1792922400.0)
        self.assertEqual(report['requests'][1]['started_at'],'2026-10-25T11:00:00+02:00')
        self.assertEqual(report['api_totals']['estimated_rub'],2.5)
        self.assertIsNone(report['totals']['estimated_rub'])
        self.assertEqual(json.dumps(api),original)

    def test_invalid_timestamp_types_and_null_counters_do_not_hide_history(self):
        api={'available':True,'totals':{'input_tokens':None,'output_tokens':None},'roles':[],
             'requests':[{'task_id':str(i),'started_at':value} for i,value in enumerate(
                 [None,True,float('inf'),float('nan'),-1,10**100,{},[], 'x'*65])]}
        report=token_usage.merge_usage(api,{})
        self.assertEqual([r['task_id'] for r in report['requests']],[str(i) for i in range(9)])
        self.assertEqual(report['totals']['total_tokens'],0)

    def test_naive_iso_timestamp_is_utc_and_matches_epoch_without_host_timezone(self):
        self.assertEqual(token_usage._timestamp('2026-10-25T10:00:00'),
                         token_usage._timestamp('2026-10-25T10:00:00Z'))

    def test_missing_codex_does_not_erase_gateway_money(self):
        api={'available':True,'totals':{'requests':1,'input_tokens':10,'estimated_rub':2},'roles':[]}
        report=token_usage.merge_usage(api,self.report())
        self.assertEqual(report['totals']['estimated_rub'],2)


if __name__=='__main__':unittest.main()
