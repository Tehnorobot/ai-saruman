from copy import deepcopy
import unittest
from common import evidence_policy, pipeline_contracts as contracts, task_graph


class EvidencePolicyTests(unittest.TestCase):
    def test_only_explicit_hash_pinned_capture_condition_is_excluded(self):
        catalog=contracts.load_example('RequirementsCatalog');graph=contracts.load_example('TaskGraph')
        condition=deepcopy(catalog['conditions'][0]);condition.update(id='CAPTURE',expected='Attach optional video of actual tree growth')
        catalog['conditions'].append(condition)
        catalog['requirements'][0]['condition_ids'].append('CAPTURE')
        graph['tasks'][0]['condition_ids'].append('CAPTURE')
        graph['tasks'].append({**deepcopy(graph['tasks'][0]),'id':'MEDIA','condition_ids':['CAPTURE'],'depends_on':['T1']})
        graph['tasks'][1]['depends_on']=['MEDIA']
        question={'question':'What format for the recording?','condition_ids':['CAPTURE'],'kind':'user_clarification',
            'status':'open','owner':'user','answer':None,'reason':'Previously asked about capture',
            'source_refs':catalog['requirements'][0]['source_refs']}
        catalog['open_questions'].append(question['question']);catalog['question_resolutions'].append(question)
        original=deepcopy(catalog)
        policy={'spec_revision':catalog['scope']['spec_revision'],
            'supplemental_conditions':{'CAPTURE':evidence_policy.definition(condition)}}
        active,updated,optional=evidence_policy.project(catalog,graph,policy)
        self.assertEqual(catalog,original)
        self.assertEqual(optional,[condition])
        self.assertEqual([c['id'] for c in active['conditions']],['C1','C2'])
        self.assertNotIn(question['question'],active['open_questions'])
        task_graph.validate_graph(updated,active)
        self.assertEqual(len(graph['tasks'])-1,len(updated['tasks']))
        self.assertEqual(updated['tasks'][1]['depends_on'],['T1'])
        policy['supplemental_conditions']['CAPTURE']='0'*64
        self.assertEqual(evidence_policy.project(catalog,graph,policy)[0],original)

