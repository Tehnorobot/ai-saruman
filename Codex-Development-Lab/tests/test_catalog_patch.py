"""Catalog deltas preserve untouched records and never mutate on rejection."""
from copy import deepcopy
import unittest
from common import catalog_patch
from common.pipeline_contracts import load_example


class CatalogPatchTests(unittest.TestCase):
    def setUp(self):
        self.catalog=load_example('RequirementsCatalog')
        self.patch=load_example('CatalogPatch')
        self.ref=self.patch['base_catalog_ref']

    def test_only_target_record_changes_and_original_is_unchanged(self):
        original=deepcopy(self.catalog)
        result=catalog_patch.apply(self.catalog,self.patch,self.ref)
        self.assertEqual(self.catalog,original)
        self.assertEqual(result['requirements'],original['requirements'])
        self.assertEqual(result['conditions'][1:],original['conditions'][1:])
        self.assertNotEqual(result['conditions'][0],original['conditions'][0])
        self.assertEqual(catalog_patch.impact(original,result)['condition_ids'],['C1'])

    def test_stale_base_rejected_without_changes(self):
        original=deepcopy(self.catalog)
        self.patch['base_catalog_ref']['sha256']='0'*64
        with self.assertRaises(ValueError):catalog_patch.apply(self.catalog,self.patch,{**self.ref,'sha256':'a'*64})
        self.assertEqual(self.catalog,original)

    def test_conflicting_duplicate_and_unknown_remove_rejected(self):
        for variant in ('both','duplicate','unknown'):
            patch=deepcopy(self.patch)
            if variant=='both':patch['conditions_remove']=['C1']
            elif variant=='duplicate':patch['conditions_upsert']*=2
            else:patch['conditions_remove']=['absent']
            original=deepcopy(self.catalog)
            with self.subTest(case=variant),self.assertRaises(ValueError):catalog_patch.apply(self.catalog,patch,self.ref)
            self.assertEqual(self.catalog,original)

    def test_child_owner_and_parent_links_must_be_changed_together(self):
        self.patch['conditions_upsert'][0]['requirement_id']='R2'
        with self.assertRaises(ValueError):catalog_patch.apply(self.catalog,self.patch,self.ref)
        r1,r2=deepcopy(self.catalog['requirements'])
        r1['condition_ids']=[];r2['condition_ids'].append('C1')
        self.patch.update(requirements_remove=['R1'],requirements_upsert=[r2])
        result=catalog_patch.apply(self.catalog,self.patch,self.ref)
        self.assertEqual(result['conditions'][0]['requirement_id'],'R2')

    def test_removal_does_not_silently_leave_orphan_parent(self):
        self.patch['conditions_upsert']=[];self.patch['conditions_remove']=['C1']
        with self.assertRaises(ValueError):catalog_patch.apply(self.catalog,self.patch,self.ref)
        self.patch['requirements_remove']=['R1']
        result=catalog_patch.apply(self.catalog,self.patch,self.ref)
        self.assertEqual(catalog_patch.impact(self.catalog,result)['removed_condition_ids'],['C1'])

    def test_source_validation_failure_is_atomic(self):
        self.patch['requirements_upsert']=[deepcopy(self.catalog['requirements'][0])]
        original=deepcopy(self.catalog)
        with self.assertRaises(ValueError):catalog_patch.apply(self.catalog,self.patch,self.ref,lambda _:b'not the cited source')
        self.assertEqual(self.catalog,original)

    def test_question_history_kept_and_resolutions_link_real_keys(self):
        self.patch['questions_add']=['Which method?']
        resolution={'question':'Which method?','kind':'implementation_decision','status':'resolved','owner':'planner',
            'condition_ids':['C2'],'answer':'Use documented method','reason':'Engineering choice',
            'source_refs':self.catalog['requirements'][1]['source_refs']}
        self.patch['question_resolutions_upsert']=[resolution]
        result=catalog_patch.apply(self.catalog,self.patch,self.ref)
        self.assertEqual(result['open_questions'],['Which method?'])
        self.assertIn('C2',catalog_patch.impact(self.catalog,result)['condition_ids'])
        resolution['question']='nonexistent'
        with self.assertRaises(ValueError):catalog_patch.apply(self.catalog,self.patch,self.ref)

    def test_patch_never_contains_arbitrary_filesystem_operations(self):
        self.patch['path']='../secret'
        with self.assertRaises(ValueError):catalog_patch.apply(self.catalog,self.patch,self.ref)

    def test_requirement_change_rechecks_unchanged_sibling_conditions(self):
        catalog=deepcopy(self.catalog)
        extra=deepcopy(catalog['conditions'][0]);extra['id']='C3';catalog['conditions'].append(extra)
        catalog['requirements'][0]['condition_ids'].append('C3')
        self.patch['conditions_upsert']=[]
        req=deepcopy(catalog['requirements'][0]);req['statement']='Changed source interpretation'
        self.patch['requirements_upsert']=[req]
        result=catalog_patch.apply(catalog,self.patch,self.ref)
        self.assertEqual(catalog_patch.impact(catalog,result)['condition_ids'],['C1','C3'])

    def test_graph_changed_dependency_rechecks_downstream_conditions(self):
        graph=load_example('TaskGraph');changed=deepcopy(graph)
        changed['tasks'][0]['outcome']='Changed navigation behavior'
        self.assertEqual(catalog_patch.graph_impact(graph,changed),['C1','C2'])


if __name__=='__main__':unittest.main()
