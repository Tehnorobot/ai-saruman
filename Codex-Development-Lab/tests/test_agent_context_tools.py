"""Cross-provider public context and recovery, no models or Minecraft."""
from contextlib import redirect_stdout
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent import dslab
from common import agent_context
from common.provider_failures import ProviderFailure


class AgentContextTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.base=Path(temporary.name);self.queue=self.base/'mailbox';self.queue.mkdir()
        self.environment=patch.dict(os.environ,{'AGENT_TASKS':str(self.queue)})
        self.environment.start();self.addCleanup(self.environment.stop)
        self.scope={'project_id':'1'*14,'spec_revision':'a'*64,'source_revision':'b'*64,'round':4}
        self.task={'project_id':'1'*14,'job_id':'2'*14,'scope':self.scope,'model':'example','provider':'minimax',
                   'role':'coder','continuation_key':'phase-key','dslab_capability':'synthetic','dslab_max_calls':8}
        # Production identity check uses the parent project directory name.
        self.root=self.base/('1'*14)/'workspace';self.root.mkdir(parents=True)
        self.executor=dslab.Executor(self.task,self.root)

    def tool(self,name,args,**kwargs):
        with redirect_stdout(io.StringIO()):return self.executor.tool(name,args,**kwargs)

    def write_json(self,path,value):
        target=self.root/path;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')

    def test_semantic_requirements_return_current_r_and_c_ids_without_legacy_fallback(self):
        self.write_json('.lab-pipeline/catalog.json',{'scope':self.scope,
            'requirements':[{'id':'R01','statement':'Current rule','condition_ids':['C01'],'source_refs':[]}],
            'conditions':[{'id':'C01','requirement_id':'R01','expected':'Current condition'}]})
        self.write_json('acceptance-catalog.json',{'requirements':[{'id':'R01','text':'OLD RULE'},{'id':'OLD','text':'legacy'}]})
        value=json.loads(self.tool('read_requirements',{'ids':['R01','C01','OLD']},readonly=True))
        self.assertEqual(value['missing_ids'],['OLD'])
        self.assertEqual(value['documents']['.lab-pipeline/catalog.json']['conditions'][0]['id'],'C01')
        self.assertNotIn('OLD RULE',json.dumps(value))
        legacy=json.loads(self.tool('read_requirements',{'ids':['OLD'],'namespace':'legacy'},readonly=True))
        self.assertEqual(legacy['missing_ids'],[])

    def test_file_listing_is_paged_and_hides_archives_from_default_code_view(self):
        for n in range(2050):(self.root/f'source-{n:04}.java').touch()
        self.write_json('.lab-memory/huge.json',{})
        self.write_json('.env',{'SYNTHETIC_SECRET':'secret'})
        first=json.loads(self.tool('list_files',{'limit':200},readonly=True))
        self.assertEqual(first['total'],2050);seen=first['items'];cursor=first['next_cursor']
        while cursor:
            page=json.loads(self.tool('list_files',{'limit':200,'cursor':cursor},readonly=True))
            seen.extend(page['items']);cursor=page['next_cursor']
        self.assertEqual(len(set(seen)),2050)
        self.assertNotIn('.lab-memory/huge.json',seen)
        all_files=json.loads(self.tool('list_files',{'namespace':'all','limit':200},readonly=True))
        self.assertIn('.lab-memory/huge.json',all_files['items'])

    def test_readonly_role_can_save_and_page_public_notes_without_writing_sources(self):
        self.executor=dslab.Executor({**self.task,'role':'planner'},self.root)
        for n in range(11):
            note=json.loads(self.tool('record_note',{'kind':'assessment','body_md':f'Observation {n}',
                'hypothesis_id':'H8','status':'inconclusive','links':['O31']},readonly=True))
        self.assertFalse((self.root/'.lab-pipeline/agent-state').exists())
        full=json.loads(self.tool('read_artifact',{'ref':note['ref']},readonly=True))
        self.assertIn('Observation 10',full['content'])
        page=agent_context.public_notes(self.root,self.scope,limit=4,public_root=self.executor.public_root)
        self.assertEqual(page['total'],11);self.assertEqual(len(page['items']),4)
        second=agent_context.public_notes(self.root,self.scope,limit=4,cursor=page['next_cursor'],public_root=self.executor.public_root)
        self.assertFalse(set(i['note_id'] for i in page['items'])&set(i['note_id'] for i in second['items']))
        with self.assertRaises(ValueError):self.tool('write_file',{'path':'bad.java','content':'bad'},readonly=True)
        with self.assertRaises(ValueError):self.tool('run_command',{'argv':[sys.executable,'-c','pass']},readonly=True)

    def test_command_timeout_retains_stdout_stderr_and_permanent_metadata(self):
        result=self.tool('run_command',{'argv':[sys.executable,'-c',
            "import sys,time; print('partial stdout',flush=True); print('partial stderr',file=sys.stderr,flush=True); time.sleep(5)"],
            'timeout_seconds':1},call_id='timeout-1')
        self.assertIn('"timed_out": true',result)
        self.assertIn('partial stdout',result);self.assertIn('partial stderr',result)
        metadata=next((self.executor.public_root/'commands').glob('*/execution.json'))
        value=json.loads(metadata.read_text());self.assertEqual(value['status'],'timeout')
        output=json.loads(self.tool('read_execution',{'execution_id':value['execution_id']},readonly=True))
        self.assertIn('partial stdout',output['streams']['stdout']['content'])
        full=json.loads(self.tool('read_artifact',{'ref':value['stream_refs']['stderr']},readonly=True))
        self.assertIn('partial stderr',full['content'])

    def test_command_call_replay_returns_saved_result_without_repeating_write(self):
        command={'argv':[sys.executable,'-c',"from pathlib import Path; p=Path('count'); p.write_text(p.read_text()+'x' if p.exists() else 'x'); print('finished')"]}
        first=self.tool('run_command',command,call_id='write-once')
        second=self.tool('run_command',command,call_id='write-once')
        self.assertEqual(first,second);self.assertEqual((self.root/'count').read_text(),'x')
        with self.assertRaises(ValueError):self.tool('run_command',{'argv':[sys.executable,'-c','pass']},call_id='write-once')

    def test_native_retry_restores_completed_public_tool_pair_without_rewriting_file(self):
        prompt='Input JSON:\n'+json.dumps({'scope':self.scope,'input':{'assignment':'same'}})
        sent=[]
        def first_send(payload,*args,**kwargs):
            sent.append(copy.deepcopy(payload))
            if len(sent)==1:return [{'type':'tool_use','id':'write-one','name':'write_file','input':{'path':'saved.java','content':'kept'}}],'tool_use'
            raise ProviderFailure('synthetic disconnect','stream_interrupted')
        with redirect_stdout(io.StringIO()),self.assertRaises(ProviderFailure):
            dslab.Executor(self.task,self.root,send=first_send).run(prompt)
        before=(self.root/'saved.java').stat().st_mtime_ns
        recovered=[]
        def second_send(payload,*args,**kwargs):
            recovered.append(copy.deepcopy(payload));return [{'type':'text','text':'continued'}],'end_turn'
        retry_prompt='Input JSON:\n'+json.dumps({'scope':self.scope,'input':{'assignment':'same'},
            'correction':{'retry_category':'stream_interrupted'}})
        with redirect_stdout(io.StringIO()):result=dslab.Executor(self.task,self.root,send=second_send).run(retry_prompt)
        self.assertEqual(result,'continued');self.assertEqual(before,(self.root/'saved.java').stat().st_mtime_ns)
        self.assertEqual(recovered[0]['messages'][1]['content'][0]['id'],'write-one')
        self.assertIn('Written saved.java',recovered[0]['messages'][2]['content'][0]['content'])

    def test_private_reasoning_is_not_persisted_and_fallback_retains_public_result(self):
        prompt='Input JSON:\n'+json.dumps({'scope':self.scope,'input':{}});count=0
        def first_send(*args,**kwargs):
            nonlocal count
            count+=1
            if count==1:return [{'type':'thinking','thinking':'PRIVATE_REASONING','signature':'PRIVATE_SIGNATURE'},
                {'type':'tool_use','id':'read-one','name':'list_files','input':{}}],'tool_use'
            raise ProviderFailure('synthetic disconnect','stream_interrupted')
        with redirect_stdout(io.StringIO()),self.assertRaises(ProviderFailure):dslab.Executor(self.task,self.root,send=first_send).run(prompt)
        checkpoint=next((self.executor.public_root/'continuations').glob('*.json'))
        self.assertNotIn('PRIVATE_',checkpoint.read_text())
        sent=[]
        def second_send(payload,*args,**kwargs):sent.append(copy.deepcopy(payload));return [{'type':'text','text':'continued'}],'end_turn'
        with redirect_stdout(io.StringIO()):dslab.Executor(self.task,self.root,send=second_send).run(prompt)
        self.assertIn('PUBLIC CONTINUATION',sent[0]['messages'][0]['content'])
        self.assertNotIn('PRIVATE_',json.dumps(sent[0]))

    def test_artifact_hash_scope_and_path_guards(self):
        (self.root/'source.txt').write_text('old\nnew\n',encoding='utf-8')
        ref=agent_context.reference(self.root,'source.txt')
        page=json.loads(self.tool('read_artifact',{'ref':ref,'selector':{'start_line':2,'end_line':2}},readonly=True))
        self.assertEqual(page['content'],'new')
        (self.root/'source.txt').write_text('changed')
        with self.assertRaises(ValueError):self.tool('read_artifact',{'ref':ref},readonly=True)
        for path in ('../outside','.env','folder/credentials'):
            with self.assertRaises(ValueError):agent_context.scoped_path(self.root,path)

    def test_historical_evidence_is_readable_without_becoming_current_proof(self):
        from common.evidence_access import register_bytes
        old={**self.scope,'source_revision':'c'*64,'round':1}
        ref=register_bytes(self.root,b'original assertion',scope=old,kind='execution_log')
        page=json.loads(self.tool('read_artifact',{'ref':ref},readonly=True))
        self.assertEqual(page['content'],'original assertion')
        self.assertEqual(page['scope']['source_revision'],'c'*64)
        other=register_bytes(self.root,b'other',scope={**old,'project_id':'3'*14},kind='execution_log')
        with self.assertRaisesRegex(ValueError,'project_id'):self.tool('read_artifact',{'ref':other},readonly=True)

    def test_all_public_notes_are_reachable_after_retry_with_causal_filters(self):
        first=json.loads(self.tool('record_note',{'kind':'hypothesis','body_md':'A concrete possible cause','condition_ids':['C51']},readonly=True))
        for n in range(12):self.tool('record_note',{'kind':'assessment','body_md':'Observation '+str(n),
            'hypothesis_id':first['hypothesis_id'],'status':'inconclusive'},readonly=True)
        page=json.loads(self.tool('query_memory',{'filters':{'hypothesis_id':first['hypothesis_id']},'limit':5},readonly=True))
        seen=[v['note_id'] for v in page['public_notes']['items']]
        while page['public_notes']['next_cursor']:
            page=json.loads(self.tool('query_memory',{'filters':{'hypothesis_id':first['hypothesis_id']},'limit':5,
                'public_cursor':page['public_notes']['next_cursor']},readonly=True))
            seen.extend(v['note_id'] for v in page['public_notes']['items'])
        self.assertEqual(len(set(seen)),13)


if __name__=='__main__':unittest.main()
