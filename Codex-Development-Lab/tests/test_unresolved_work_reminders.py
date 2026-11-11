"""Runner gaps remain visible without adding scheduling gates or model turns."""
from copy import deepcopy
import json
import unittest

from common import acceptance, pipeline_memory
from worker import pipeline
from tests import test_pipeline_cycle as fixture


class UnresolvedWorkReminderTests(unittest.TestCase):
    setUp=fixture.CycleTest.setUp
    verified=fixture.CycleTest.verified

    def prepare(self):
        cycle=pipeline.Cycle(self.engine,self.job,self.ws,self.out,1)
        catalog,graph=cycle.decomposition()
        decision,graph=cycle.plan(catalog,graph,{})
        _,manifest,_=cycle.code_and_review(catalog,graph,decision)
        return cycle,catalog,graph,decision,manifest

    def record_gap(self,cycle,catalog,manifest,decision,actual='Test player identity changed'):
        manifest=deepcopy(manifest)
        manifest['scope']['source_revision']=acceptance.source_fingerprint(self.ws)
        result,legacy=self.verified(self.ws,cycle.stage,self.job['id'],catalog,manifest,decision)
        scenario=result['scenario_results'][0]
        scenario.update(status='NOT_EXECUTED',attempted=True)
        scenario['checkpoints'][2].update(status='NOT_EXECUTED',actual=actual)
        scenario['fixture_result']={'status':'FAIL','reason_code':'invalid_fixture',
            'checks':[{'classname':'Fixture','name':'samePlayer','status':'FAIL','actual':actual,
                'report_refs':[],'checkpoint_ids':['CP3']}],
            'raw_checkpoints':deepcopy(scenario['checkpoints']),'affected_checkpoint_ids':['CP3']}
        cycle.record_verification(catalog,manifest,decision,result,legacy)
        return result

    def reminders(self,cycle):
        return json.loads(pipeline.source_bytes(self.ws,cycle.state['unresolved_work']['index_ref']))['reminders']

    def select_first_task(self):
        original=self.engine.request_agent
        def request(*args,**kwargs):
            result=original(*args,**kwargs)
            if kwargs['output_contract']=='PlannerDecision':
                path=args[4];events=[json.loads(line) for line in path.read_text().splitlines()]
                value=json.loads(events[-1]['item']['text'])
                value['actions'][0].update(task_ids=['T1'],condition_ids=['C1'])
                value['regression_condition_ids']=[]
                events[-1]['item']['text']=json.dumps(value)
                path.write_text('\n'.join(json.dumps(e) for e in events),encoding='utf-8')
            return result
        self.engine.request_agent=request

    def test_pure_fixture_gap_has_checkpoint_history_and_exact_result_links(self):
        cycle,catalog,graph,decision,manifest=self.prepare()
        self.record_gap(cycle,catalog,manifest,decision)
        summary,ref=cycle.refresh_condition_progress(catalog,graph)
        condition=next(c for c in summary['conditions'] if c['condition_id']=='C2')
        checkpoint=condition['checkpoints'][0]
        self.assertEqual(checkpoint['unresolved_observations'],0)
        self.assertEqual(checkpoint['unresolved_gaps'],1)
        self.assertEqual(checkpoint['latest_fixture_status'],'FAIL')
        trace=json.loads(pipeline.source_bytes(self.ws,checkpoint['history_ref']))
        result=pipeline_memory.read_payload(self.out,trace['history'][0]['result_ref']['record_id'])
        self.assertIn('identity changed',result['scenario_results'][0]['fixture_result']['checks'][0]['actual'])
        reminder=self.reminders(cycle)[0]
        self.assertEqual((reminder['scenario_id'],reminder['checkpoint_id']),('s1','CP3'))
        self.assertEqual(reminder['condition_ids'],['C2'])
        self.assertEqual(reminder['task_ids'],['T2'])
        self.assertIn('executable setup',reminder['next_step_advice'])

    def test_omitted_gap_is_recorded_and_next_planner_not_retried_or_paused(self):
        cycle,catalog,graph,decision,manifest=self.prepare()
        previous=self.record_gap(cycle,catalog,manifest,decision)
        self.select_first_task()
        resumed=pipeline.Cycle(self.engine,self.job,self.ws,self.out,2)
        before=len(self.engine.requests)
        accepted,_=resumed.plan(catalog,graph,previous)
        self.assertEqual(len(self.engine.requests)-before,1)
        self.assertEqual(accepted['actions'][0]['condition_ids'],['C1'])
        reminder=self.reminders(resumed)[0]
        self.assertEqual(reminder['unscheduled_condition_ids'],['C2'])
        self.assertEqual(reminder['scheduled_condition_ids'],[])
        request=self.engine.requests[-1][2]['input']
        summary=json.loads(pipeline.source_bytes(self.ws,request['condition_progress_ref']))
        self.assertEqual(summary['selection_advice']['unresolved_work_count'],1)
        self.assertTrue(summary['selection_advice']['unresolved_work_ref'])
        self.assertFalse(any(x.get('status')=='USER_PAUSED' for x in self.engine.updates))
        self.assertTrue(any(pipeline_memory.read_payload(self.out,m['record_id']).get('artifact_type')=='UnresolvedWorkReminders'
            for m in pipeline_memory.index_entries(self.out) if m['kind']=='reflection'))

    def test_changed_source_and_neighbour_pass_do_not_clear_fixture_gap(self):
        cycle,catalog,graph,decision,manifest=self.prepare()
        self.record_gap(cycle,catalog,manifest,decision)
        identity=self.reminders(cycle)[0]['reminder_id']
        (self.ws/'existing.java').write_text('A changed independent feature')
        self.record_gap(cycle,catalog,manifest,decision,actual='Different diagnostic wording')
        reminder=self.reminders(cycle)[0]
        self.assertEqual(reminder['reminder_id'],identity)
        self.assertEqual(reminder['observations'][0]['unresolved_gaps'],2)
        trace=json.loads(pipeline.source_bytes(self.ws,reminder['history_refs'][0]))
        self.assertEqual(len(trace['history']),2)

    def test_same_checkpoint_measurement_closes_reminder_keeps_original_history(self):
        cycle,catalog,graph,decision,manifest=self.prepare()
        self.record_gap(cycle,catalog,manifest,decision)
        old=self.reminders(cycle)[0]['history_refs'][0]
        result,legacy=self.verified(self.ws,cycle.stage,self.job['id'],catalog,manifest,decision)
        cycle.record_verification(catalog,manifest,decision,result,legacy)
        self.assertEqual(self.reminders(cycle),[])
        self.assertEqual(len(json.loads(pipeline.source_bytes(self.ws,old))['history']),1)

    def test_explicit_deferral_survives_with_reason_and_return_condition(self):
        cycle,catalog,graph,decision,manifest=self.prepare()
        self.record_gap(cycle,catalog,manifest,decision)
        summary,_=cycle.refresh_condition_progress(catalog,graph)
        decision=deepcopy(decision)
        decision['actions'][0].update(task_ids=['T1'],condition_ids=['C1'])
        decision['regression_condition_ids']=[]
        note={'task_id':'T2','reason':'Use the exact fixture log to stabilize player identity',
            'return_when':'Run the two-JVM scenario with the same player'}
        decision['deferred']=[note]
        cycle.remember_unresolved_work(summary,graph,decision)
        reminder=self.reminders(cycle)[0]
        self.assertEqual(reminder['planner_deferrals'],[note])
        self.assertEqual(reminder['explicitly_deferred_condition_ids'],['C2'])
        self.assertEqual(reminder['unscheduled_condition_ids'],[])
        cycle.state['return_agenda']=[note]
        cycle.save()
        resumed=pipeline.Cycle(self.engine,self.job,self.ws,self.out,2)
        resumed.refresh_condition_progress(catalog,graph)
        self.assertEqual(self.reminders(resumed)[0]['planner_deferrals'],[note])

    def test_accepted_coder_checkpoint_gets_reminder_without_repeating_completed_code(self):
        cycle,catalog,graph,decision,manifest=self.prepare()
        self.record_gap(cycle,catalog,manifest,decision)
        before=sum(name=='CoderReport' for name,_,_ in self.engine.requests)
        resumed=pipeline.Cycle(self.engine,self.job,self.ws,self.out,1)
        resumed.code_and_review(catalog,graph,decision)
        self.assertEqual(sum(name=='CoderReport' for name,_,_ in self.engine.requests),before)
        saved=pipeline.read_json(resumed.stage,'pipeline/coding-state.json')
        progress=json.loads(pipeline.source_bytes(self.ws,saved['assignment']['condition_progress_ref']))
        self.assertEqual(progress['selection_advice']['unresolved_work_count'],1)


if __name__=='__main__':unittest.main()
