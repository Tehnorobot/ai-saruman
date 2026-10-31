"""Correction reads separate the immutable answer from trusted task metadata."""
from copy import deepcopy
import json
import unittest

from common import submission_patch as patch


class PatchFieldContextTests(unittest.TestCase):
    def setUp(self):
        self.base={'actions':[{'task_ids':['T2']}],'deferred':[]}
        self.context={'allowed_paths':['$.actions[0].task_ids'],
                      'task_index':[{'id':'T1','depends_on':[],'condition_ids':['C1']},
                                    {'id':'T2','depends_on':['T1'],'condition_ids':['C2']}]}

    def test_trusted_index_is_readable_without_becoming_an_answer_field(self):
        original=deepcopy((self.base,self.context))
        value=patch.read_field(self.base,'AgentPlan',{'section':'context','path':'$.task_index'},self.context)
        self.assertEqual(value['value'],self.context['task_index'])
        self.assertEqual((self.base,self.context),original)
        self.assertNotIn('task_index',self.base)

    def test_wrong_section_explains_where_index_lives(self):
        with self.assertRaises(patch.PatchFieldError) as caught:
            patch.read_field(self.base,'AgentPlan',{'path':'$.task_index'},self.context)
        d=caught.exception.diagnostic
        self.assertEqual(d['requested_path'],'$.task_index')
        self.assertEqual(d['section'],'value')
        self.assertEqual(d['allowed_patch_paths'],self.context['allowed_paths'])
        self.assertIn('task_index',d['context_root_fields'])
        self.assertNotIn('task_index',d['value_root_fields'])
        self.assertIn('section="context"',d['instruction'])
        self.assertEqual(json.loads(str(caught.exception)),d)

    def test_value_format_and_invalid_section_do_not_guess(self):
        self.assertEqual(patch.read_field(self.base,'AgentPlan',{'path':'$.actions[0].task_ids'},self.context)['value'],['T2'])
        self.assertIn('type',patch.read_field(self.base,'AgentPlan',{'section':'format','path':'$.actions[0].task_ids'},self.context)['value'])
        with self.assertRaises(patch.PatchFieldError):
            patch.read_field(self.base,'AgentPlan',{'section':'files','path':'$.task_index'},self.context)
        with self.assertRaises(patch.PatchFieldError):
            patch.read_field(self.base,'AgentPlan',{'section':'context','path':'$.task_index[999]'},self.context)

    def test_large_context_can_be_read_completely_by_paging(self):
        self.context['task_index']*=500
        offset=0;fragments=[]
        while True:
            result=patch.read_field(self.base,'AgentPlan',{
                'section':'context','path':'$.task_index','offset':offset,'limit':16000},self.context)
            fragments.append(result['json_fragment'])
            if result['next_offset'] is None:break
            offset=result['next_offset']
        self.assertEqual(json.loads(''.join(fragments)),self.context['task_index'])


if __name__=='__main__':unittest.main()
