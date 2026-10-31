import copy
import json
from pathlib import Path
import tempfile
import unittest

from common import acceptance_progress, work_routing


class WorkRoutingTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.catalog={'spec_sha256':'a'*64,'requirements':[{'id':rid} for rid in ('failed','new-one','new-two')]}
        self.scenario={'id':'combat','kind':'unit','task':'testCombat','requirement_ids':['failed'],
            'description':'Combat assertion','expected_tests':[{'classname':'CombatTest','name':'fight'}]}
        self.plan={'requirements':[{'id':'failed','kind':'unit','scenario_ids':['combat']},
            {'id':'new-one','kind':'manual','scenario_ids':[]},
            {'id':'new-two','kind':'manual','scenario_ids':[]}],'scenarios':[self.scenario],'notes':[]}
        self.assignment={'current_task':{'requirement_ids':['failed','new-one','new-two']},
            'failure_memory':{'combat':{'detail':'Do not erase me'}}}
        self.ledger={'scenarios':{'combat':{'history':[]}}}
        self.previous={'checks':[{'label':'gradle_build','status':'PASS'}]}

    def completed(self,number,target='combat',kind='repair_scenario',status='FAIL',model=True):
        folder=self.root/f'round-{number:02d}';folder.mkdir(exist_ok=True)
        action={'kind':kind,'target':target}
        result={'id':'combat','requirement_ids':['failed'],'status':status,
            'test_status':'FAIL' if status=='FAIL' else 'PASS',
            'assertion_evidence':{'reports':[{'file':'actual.xml'}],
                'expected_tests':copy.deepcopy(self.scenario['expected_tests']),'missing_tests':[]}}
        report={**self.previous,'acceptance_results':{'scenarios':[result]}}
        (folder/'verification_complete.json').write_text(json.dumps(report))
        (folder/'development_complete.json').write_text(json.dumps({'kind':'model' if model else 'verification_only'}))
        (folder/'round-memory-start.json').write_text(json.dumps({'round_action':action,'round_action_round':number}))
        (folder/'acceptance_results.json').write_text(json.dumps({'scenarios':[result]}))
        entry=self.ledger['scenarios']['combat'];digest=acceptance_progress.definition_digest(self.scenario)
        entry['history'].append({'round':number,'definition_sha256':digest,'status':status})
        entry['latest']={'round':number,'definition_sha256':digest,'result':result}
        return folder

    def choose(self,number):
        return work_routing.choose_directive(self.root,self.catalog,self.plan,self.assignment,
            self.ledger,number,self.previous)

    def test_two_failures_rotate_then_return_and_later_choose_next_requirement(self):
        self.completed(1)
        self.assertEqual(self.choose(2)['kind'],'normal')
        self.completed(2)
        rotation=self.choose(3)
        self.assertEqual((rotation['kind'],rotation['required_target']),('rotate_requirement','new-one'))
        self.completed(3,'new-one','implement_requirement')
        self.assertEqual(self.choose(4)['required_target'],'combat')
        self.completed(4)
        self.assertEqual(self.choose(5)['kind'],'normal')
        self.completed(5)
        self.assertEqual(self.choose(6)['required_target'],'new-two')

    def test_replay_is_idempotent_and_does_not_advance_return_obligation(self):
        self.completed(1);self.completed(2)
        rotation=self.choose(3);before=(self.root/work_routing.NAME).read_bytes()
        self.assertEqual(self.choose(3),rotation)
        self.assertEqual((self.root/work_routing.NAME).read_bytes(),before)
        self.assertEqual(self.choose(4)['kind'],'return_to_failure')
        self.assertEqual(self.choose(5)['kind'],'return_to_failure')

    def test_verification_only_and_side_failure_do_not_consume_return(self):
        self.completed(1);self.completed(2);self.choose(3)
        self.completed(3,'new-one','implement_requirement')
        self.completed(4,model=False)
        self.assignment['failure_memory']['side']={'detail':'New issue'}
        directive=self.choose(5)
        self.assertEqual(directive['kind'],'return_to_failure')
        self.assertEqual(directive['required_target'],'combat')

    def test_current_definition_and_exact_assertions_are_required(self):
        self.completed(1);folder=self.completed(2)
        raw=json.loads((folder/'acceptance_results.json').read_text())
        raw['scenarios'][0]['assertion_evidence']['expected_tests'][0]['name']='wrong'
        (folder/'acceptance_results.json').write_text(json.dumps(raw))
        self.assertEqual(self.choose(3)['kind'],'normal')
        self.scenario['description']='Changed definition'
        self.assertEqual(self.choose(4)['kind'],'normal')

    def test_planner_action_takes_precedence_over_memory(self):
        self.completed(1);folder=self.completed(2)
        (folder/'planner').mkdir()
        (folder/'planner/plan.json').write_text(json.dumps({'action':{'kind':'implement_requirement','target':'new-one'}}))
        self.assertEqual(self.choose(3)['kind'],'normal')

    def test_explicit_manual_excluded_but_default_manual_is_not(self):
        self.plan['requirements'][1]['manual_review']={
            'category':'physical_device','reason':'Test the physical controller connected to the user computer.',
            'automation_blocker':'The isolated test environment cannot access the physical controller.',
            'steps':['Connect the controller.'],'expected_result':'It vibrates once.'}
        self.completed(1);self.completed(2)
        self.assertEqual(self.choose(3)['required_target'],'new-two')

    def test_build_plan_and_infrastructure_block_diversion(self):
        self.completed(1);self.completed(2)
        for index,label in enumerate(('gradle_build','production_jar','verification_session','acceptance_plan'),3):
            self.previous={'checks':[{'label':label,'status':'ERROR'}]}
            self.assertEqual(self.choose(index)['kind'],'normal')
        self.previous={'checks':[]};self.plan['notes']=['Rejected plan']
        self.assertEqual(self.choose(8)['kind'],'normal')

    def test_failed_assertions_and_assignment_are_not_mutated(self):
        self.completed(1);self.completed(2)
        ledger=copy.deepcopy(self.ledger);assignment=copy.deepcopy(self.assignment)
        self.choose(3)
        self.assertEqual(self.ledger,ledger);self.assertEqual(self.assignment,assignment)

    def test_no_unmapped_requirement_is_repeated_after_inventory_exhaustion(self):
        self.completed(1);self.completed(2);self.choose(3)
        self.completed(3,'new-one','implement_requirement')
        self.completed(4);self.completed(5)
        self.assertEqual(self.choose(6)['required_target'],'new-two')
        self.completed(6,'new-two','implement_requirement')
        self.completed(7);self.completed(8)
        self.assertEqual(self.choose(9)['kind'],'normal')

    def test_build_failure_cannot_consume_obligation_to_return(self):
        self.completed(1);self.completed(2);self.choose(3)
        self.completed(3,'new-one','implement_requirement')
        folder=self.completed(4)
        report=json.loads((folder/'verification_complete.json').read_text())
        report['checks']=[{'label':'gradle_build','status':'FAIL'}]
        (folder/'verification_complete.json').write_text(json.dumps(report))
        self.assertEqual(self.choose(5)['kind'],'return_to_failure')

    def test_reservation_prioritizes_new_check_and_keeps_original_failure(self):
        self.completed(1);self.completed(2);directive=self.choose(3)
        new={**self.scenario,'id':'new-check','requirement_ids':['new-one']}
        extras=[{**self.scenario,'id':'extra-'+str(i)} for i in range(4)]
        self.plan['scenarios'].extend([new]+extras)
        request={'scenarios':copy.deepcopy(extras)};before=copy.deepcopy(request)
        selected=work_routing.reserve_regression(request,self.plan,directive)
        self.assertEqual([s['id'] for s in selected['scenarios'][:2]],['new-check','combat'])
        self.assertEqual(len(selected['scenarios']),4)
        self.assertEqual(request,before)

    def test_public_read_is_spec_bound_read_only_and_missing_does_not_create_state(self):
        self.assertEqual(work_routing.read_directive(self.root,self.catalog,3)['kind'],'normal')
        self.assertFalse((self.root/work_routing.NAME).exists())
        self.completed(1);self.completed(2);expected=self.choose(3)
        path=self.root/work_routing.NAME;before=path.read_bytes()
        self.assertEqual(work_routing.read_directive(self.root,self.catalog,3),expected)
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual(work_routing.read_directive(self.root,{'spec_sha256':'b'*64},3)['kind'],'normal')
        self.assertEqual(work_routing.read_directive(self.root,self.catalog,4)['kind'],'normal')


if __name__=='__main__':unittest.main()
