from copy import deepcopy
import json
import unittest
from common import submission_patch as patch, preparation
from common.pipeline_contracts import ContractError,load_example


def change(base,path,old,new,exists=True):
    return {'contract':'SubmissionPatch','version':'v1','submission_sha256':patch.fingerprint(base),
        'changes':[{'path':path,'operation':'set','expected_exists':exists,
            'expected_old_json':json.dumps(old),'new_json':json.dumps(new)}]}


class SubmissionPatchTests(unittest.TestCase):
    def test_required_root_feedback_expands_only_missing_fields_without_full_example(self):
        base={'contract':'PreparationHandoff','scope':{'round':12},'unrelated':'x'*100000}
        issues=[{'path':'$','code':'required','expected':'required fields: catalog_ref, graph_ref',
            'example_json':'x'*50000}]
        context=patch.context(base,issues,'PreparationHandoff')
        self.assertEqual(context['allowed_paths'],['$.catalog_ref','$.graph_ref'])
        self.assertLess(len(json.dumps(context)),12000)
        self.assertIsNone(context['issues'][0]['example_json'])
        self.assertEqual(context['fields'][0]['expected_format']['value']['type'],'object')

    def test_only_error_field_changes_and_original_is_immutable(self):
        base={'scope':{'round':'12'},'decisions':['keep'],'nested':{'text':'unchanged'}}
        saved=deepcopy(base)
        result=patch.apply(base,change(base,'$.scope.round','12',12),['$.scope.round'])
        self.assertEqual(base,saved)
        self.assertEqual(result,{**base,'scope':{'round':12}})
        with self.assertRaises(ValueError):patch.apply(base,change(base,'$.decisions',['keep'],['changed']),['$.scope.round'])

    def test_stale_or_forged_old_values_and_root_are_rejected(self):
        base={'value':'bad'}
        for modify in ('digest','old','root','nan'):
            value=change(base,'$.value','bad',12)
            if modify=='digest':value['submission_sha256']='0'*64
            elif modify=='old':value['changes'][0]['expected_old_json']='"different"'
            elif modify=='root':value['changes'][0]['path']='$'
            else:value['changes'][0]['new_json']='NaN'
            with self.subTest(modify=modify),self.assertRaises(ValueError):patch.apply(base,value,['$.value'])
        self.assertEqual(base,{'value':'bad'})
        with self.assertRaises(ValueError):
            patch.apply({'tasks':[]},change({'tasks':[]},'$.tasks[999].status',None,'ready',False),['$.tasks'])

    def test_add_missing_field_and_duplicate_or_overlapping_edits(self):
        base={'scope':{'round':'12'}}
        result=patch.apply(base,change(base,'$.notes',None,[],False),['$.notes'])
        self.assertEqual(result['notes'],[])
        value=change(base,'$.scope',base['scope'],{'round':12})
        value['changes'].append(change(base,'$.scope.round','12',12)['changes'][0])
        with self.assertRaises(ValueError):patch.apply(base,value,['$.scope'])
        value['changes'][1]=deepcopy(value['changes'][0])
        with self.assertRaises(ValueError):patch.apply(base,value,['$.scope'])

    def test_T1_A_has_exact_missing_and_unknown_then_only_its_ID_is_repaired(self):
        graph=load_example('TaskGraph');catalog=load_example('RequirementsCatalog')
        base=load_example('PreparationHandoff')
        for task in base['tasks']:task.update(status='ready',experiment=None)
        base['tasks'][0]['task_id']='T1-A';saved=deepcopy(base)
        with self.assertRaises(ContractError) as caught:
            preparation.validate_handoff(base,graph,catalog,base['catalog_ref'],base['graph_ref'],[])
        issue=caught.exception.issues[0]
        self.assertEqual(issue['path'],'$.tasks[0].task_id')
        self.assertIn('"unknown_ids": ["T1-A"]',issue['expected'])
        self.assertIn('"missing_ids": ["T1"]',issue['expected'])
        fixed=patch.apply(base,change(base,issue['path'],'T1-A','T1'),patch.allowed_paths(caught.exception.issues))
        preparation.validate_handoff(fixed,graph,catalog,fixed['catalog_ref'],fixed['graph_ref'],[])
        saved['tasks'][0]['task_id']='T1'
        self.assertEqual(fixed,saved)

