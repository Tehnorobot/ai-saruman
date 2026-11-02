"""Scheduling history survives changed prose, source edits and unrelated passes."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from common import condition_progress as progress, pipeline_memory as memory


class ConditionProgressTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.scope={'project_id':'project','spec_revision':'a'*64,'source_revision':'b'*64,'round':1}
        self.catalog={'conditions':[{'id':'C1','requirement_id':'R1'},
                                    {'id':'C2','requirement_id':'R2'}]}
        self.snapshots={};self.last_plan=None

    def ref(self):return {'path':'evidence.json','sha256':'e'*64,'media_type':'application/json'}

    def attempt(self,number,*,status='FAIL',neighbour='PASS',text='failed',source=None,
                hypothesis='Hypothesis one',checkpoint='CP1',immutable_mapping=True,build='PASS',omit_scenarios=False):
        scope={**self.scope,'round':number,'source_revision':source or format(number,'064x')}
        plan={'contract':'PlannerDecision','scope':scope,'rationale':hypothesis,'hypotheses':[],
              'actions':[{'kind':'repair','condition_ids':['C1'],'goal':'Test the cause',
                          'expected_observation':'A discriminating measurement'}]}
        links=[]
        if self.last_plan:links=[{'relation':'responds_to','record_id':self.last_result}]
        p=memory.append_event(self.root,None,'plan',plan,scope=scope,producer='planner',links=links)
        self.last_plan=p['record_id']
        coder={'contract':'CoderReport','changed_files':[self.ref()], 'summary':'UNVERIFIED changed implementation'}
        c=memory.append_event(self.root,None,'coder',coder,scope=scope,producer='coder')
        manifest={'scope':scope,'scenarios':[{'id':'scenario','checkpoints':[
            {'id':checkpoint,'condition_ids':['C1'],'assertion':'Required real action and effect',
             'observable':'actualEffect','comparator':'eq','expected':True},
            {'id':'CP2','condition_ids':['C2'],'assertion':'Other behaviour'}]}]}
        result={'contract':'VerificationResult','scope':scope,'runner_run_id':'run-'+str(number),
            'request_ref':self.ref(),'build_status':build,'scenario_results':[{
                'scenario_id':'scenario','status':'PASS' if status==neighbour=='PASS' else 'FAIL',
                'checkpoints':[{'checkpoint_id':checkpoint,'status':status,'actual':text},
                               {'checkpoint_id':'CP2','status':neighbour,'actual':'other result'}]}]}
        if omit_scenarios:result['scenario_results']=[]
        r=memory.append_event(self.root,None,'verification',result,scope=scope,producer='runner',links=[
            {'relation':'tests_hypothesis','record_id':p['record_id']},
            {'relation':'verifies','record_id':c['record_id']}])
        self.last_result=r['record_id']
        if immutable_mapping:
            memory.append_event(self.root,None,'reflection',{'artifact_type':'ConditionObservationMapping',
                'verification_record_id':r['record_id'],'manifest':manifest},scope=scope,producer='controller')
        else:self.snapshots[number]=manifest
        return manifest,result,r

    def rebuild(self):
        return progress.reconstruct(self.root,self.catalog,self.scope,
            lambda scope:self.snapshots.get(scope['round']),last_plan_id=self.last_plan)

    def condition(self,cid='C1'):return next(p for p in self.rebuild()['conditions'] if p['condition_id']==cid)

    def test_changed_message_source_and_neighbour_never_hide_repeated_checkpoint(self):
        self.attempt(1,text='timeout: first wording',neighbour='FAIL')
        self.attempt(2,text='assertion: entirely different prose',source='f'*64)
        p=self.condition();cp=p['checkpoints'][0]
        self.assertTrue(p['repeated_unresolved'])
        self.assertEqual(cp['unresolved_observations'],2)
        self.assertEqual(cp['failures_since_pass'],2)
        self.assertEqual(cp['exact_proposal_repeats'],1)
        self.assertFalse(self.condition('C2')['repeated_unresolved'])

    def test_same_checkpoint_pass_closes_current_streak_without_erasing_history(self):
        self.attempt(1);self.attempt(2);self.attempt(3,status='PASS')
        p=self.condition()
        self.assertFalse(p['repeated_unresolved'])
        self.assertEqual(len(p['history']),3)
        self.assertEqual(p['checkpoints'][0]['last_verified_round'],3)
        self.attempt(4)
        self.assertEqual(self.condition()['checkpoints'][0]['unresolved_observations'],1)

    def test_skipped_execution_does_not_clear_prior_failure(self):
        self.attempt(1)
        self.attempt(2,status='NOT_EXECUTED',neighbour='NOT_EXECUTED')
        p=self.condition()
        self.assertTrue(p['repeated_unresolved'])
        self.assertEqual(p['checkpoints'][0]['failures_since_pass'],1)

    def test_renamed_checkpoint_does_not_silently_close_old_failure(self):
        self.attempt(1);self.attempt(2)
        self.attempt(3,status='PASS',checkpoint='new-name')
        p=self.condition()
        self.assertTrue(p['repeated_unresolved'])
        self.assertEqual(len(p['checkpoints']),2)

    def test_original_hypotheses_changes_outcomes_and_followup_are_linked(self):
        _,_,first=self.attempt(1,hypothesis='Full unabridged initial hypothesis')
        self.attempt(2,hypothesis='Measurement refuted the initial hypothesis; new experiment')
        p=self.condition();row=p['history'][0]
        self.assertEqual(row['result_ref']['record_id'],first['record_id'])
        self.assertEqual(len(row['followup_analysis_refs']),1)
        plan=memory.read_payload(self.root,row['plan_ref']['record_id'])
        self.assertEqual(plan['rationale'],'Full unabridged initial hypothesis')
        coder=memory.read_payload(self.root,row['coder_ref']['record_id'])
        self.assertEqual(coder['changed_files'][0]['path'],'evidence.json')
        self.assertEqual(memory.read_payload(self.root,row['result_ref']['record_id'])['scenario_results'][0]['checkpoints'][0]['actual'],'failed')

    def test_historical_controller_snapshots_restore_existing_attempts(self):
        self.attempt(1,immutable_mapping=False);self.attempt(2,immutable_mapping=False)
        self.assertTrue(self.condition()['repeated_unresolved'])

    def test_missing_or_wrong_revision_mapping_is_reported_not_guessed(self):
        self.attempt(1,immutable_mapping=False)
        self.snapshots[1]['scope']['source_revision']='f'*64
        p=self.rebuild()
        self.assertEqual(len(p['unmapped_verification_refs']),1)
        self.assertEqual(p['conditions'][0]['observation_count'],0)

    def test_another_spec_or_project_cannot_inherit_progress(self):
        self.attempt(1);self.attempt(2)
        for key,value in [('project_id','another'),('spec_revision','c'*64)]:
            scope={**self.scope,key:value}
            p=progress.reconstruct(self.root,self.catalog,scope,lambda _:None)
            self.assertFalse(any(c['history'] for c in p['conditions']))

    def test_replayed_runner_result_is_not_another_independent_attempt(self):
        _,result,record=self.attempt(1)
        old=memory.load_record(self.root,record['record_id'])
        memory.append_event(self.root,None,'verification',result,scope=result['scope'],
            producer='runner',links=old['entry']['links'])
        self.assertEqual(self.condition()['observation_count'],1)

    def test_unselected_scenario_does_not_add_inconclusive_attempts(self):
        manifest,result,_=self.attempt(1)
        result['scenario_results']=[]
        self.assertEqual(progress.observations(manifest,result),[])

    def test_assigned_but_never_observed_condition_has_an_explicit_gap(self):
        self.attempt(1,omit_scenarios=True);self.attempt(2,omit_scenarios=True)
        p=self.condition();gap=p['checkpoints'][0]
        self.assertTrue(p['repeated_unresolved'])
        self.assertIsNone(gap['scenario_id'])
        self.assertEqual(gap['unresolved_observations'],2)
        self.attempt(3)
        p=self.condition();gap=next(cp for cp in p['checkpoints'] if cp['scenario_id'] is None)
        self.assertEqual(gap['last_status'],'OBSERVATION_AVAILABLE')
        self.assertEqual(gap['unresolved_observations'],0)
        actual=next(cp for cp in p['checkpoints'] if cp['scenario_id']=='scenario')
        self.assertEqual(actual['last_status'],'FAIL')

    def test_successful_checkpoint_inside_failed_build_is_not_progress(self):
        manifest,result,_=self.attempt(1,status='PASS',neighbour='PASS',build='FAIL')
        rows=progress.observations(manifest,result)
        self.assertTrue(all(r['status']=='NOT_EXECUTED' for r in rows))

    def test_changed_assertion_is_visible_to_semantic_review_without_reset(self):
        manifest,result,record=self.attempt(1,immutable_mapping=False)
        self.snapshots[1]['scenarios'][0]['checkpoints'][0]['assertion']='Old stronger observable'
        self.attempt(2)
        self.assertTrue(self.condition()['checkpoints'][0]['contract_changed'])
        self.assertEqual(self.condition()['checkpoints'][0]['unresolved_observations'],2)

    def test_graph_advice_exposes_independent_features_without_forcing_count(self):
        self.attempt(1);self.attempt(2)
        graph={'tasks':[
            {'id':'A','title':'Unresolved','condition_ids':['C1'],'depends_on':[]},
            {'id':'B','title':'New feature','condition_ids':['C2'],'depends_on':[]},
            {'id':'C','title':'Depends on unresolved','condition_ids':['C2'],'depends_on':['A']}]}
        advice=progress.selection_advice(self.rebuild(),graph,[
            {'condition_id':'C1','status':'failed'},{'condition_id':'C2','status':'untested'}])
        self.assertEqual([x['task_id'] for x in advice['independent_options']],['B'])
        self.assertEqual(advice['dependent_options'][0]['unresolved_failure_dependencies'],['A'])
        self.assertEqual(advice['open_return_task_ids'],['A'])
        verified=progress.selection_advice(self.rebuild(),graph,[
            {'condition_id':'C1','status':'verified'},{'condition_id':'C2','status':'untested'}])
        self.assertEqual(verified['dependent_options'],[])
        self.assertEqual({x['task_id'] for x in verified['independent_options']},{'B','C'})


if __name__=='__main__':unittest.main()
