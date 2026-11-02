"""Display past success separately from the current independent verdict."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from common import verification_display as display
from common.memory_archive import write_json


class VerificationDisplayTests(unittest.TestCase):
    def history(self,*results):
        return display.summarize({'history':[{'round':n,'source_revision':str(n),
            'result_ref':{'record_id':'M'+str(n)},'status':status} for n,status in results]})

    def view(self,status='untested'):
        return {'scope':{'project_id':'p','spec_revision':'a'*64},
            'status':{'coverage':[{'condition_id':'C1','status':status}]},
            'graph':{'tasks':[{'id':'T1','condition_ids':['C1']}]}}

    def test_pass_then_interrupted_rerun_is_retest_without_rewriting_verdict(self):
        view=self.view();before=deepcopy(view)
        value=display.build(view,{'C1':self.history((73,'PASS'),(74,'NOT_EXECUTED'))})
        self.assertEqual(value['conditions']['C1']['state'],'needs_retest')
        self.assertEqual(value['tasks']['T1']['last_common_success']['round'],73)
        self.assertEqual(value['tasks']['T1']['latest']['outcome'],'incomplete')
        self.assertEqual(view,before)

    def test_real_failure_remains_red_even_when_a_previous_run_passed(self):
        value=display.build(self.view('failed'),{'C1':self.history((73,'PASS'),(74,'FAIL'))})
        self.assertEqual(value['tasks']['T1']['state'],'failed')
        self.assertEqual(value['conditions']['C1']['last_success']['round'],73)

    def test_never_run_and_incomplete_first_run_are_distinct(self):
        never=display.build(self.view(),{})
        self.assertEqual(never['conditions']['C1']['state'],'untested')
        self.assertFalse(never['tasks']['T1']['has_history'])
        incomplete=display.build(self.view(),{'C1':self.history((74,'NOT_EXECUTED'))})
        self.assertEqual(incomplete['conditions']['C1']['state'],'incomplete')
        self.assertIsNone(incomplete['tasks']['T1']['last_common_success'])

    def test_only_current_independent_success_is_green(self):
        self.assertEqual(display.build(self.view('verified'),{})['tasks']['T1']['state'],'verified')
        self.assertEqual(display.build(self.view(),{'C1':self.history((73,'PASS'))})['tasks']['T1']['state'],'needs_retest')

    def test_successes_in_different_runs_do_not_invent_a_full_task_pass(self):
        view=self.view();view['graph']['tasks'][0]['condition_ids'].append('C2')
        view['status']['coverage'].append({'condition_id':'C2','status':'untested'})
        histories={'C1':self.history((73,'PASS'),(74,'FAIL')),
                   'C2':self.history((73,'FAIL'),(74,'PASS'))}
        task=display.build(view,histories)['tasks']['T1']
        self.assertIsNone(task['last_common_success'])
        self.assertEqual(task['latest']['outcome'],'failed')

    def test_partial_selection_is_not_reported_as_a_full_success(self):
        view=self.view();view['graph']['tasks'][0]['condition_ids'].append('C2')
        view['status']['coverage'].append({'condition_id':'C2','status':'untested'})
        task=display.build(view,{'C1':self.history((75,'PASS')),'C2':self.history((73,'PASS'))})['tasks']['T1']
        self.assertEqual(task['latest']['reason'],'partial_selection')
        self.assertEqual(task['latest']['outcome'],'incomplete')

    def test_cached_snapshot_is_hash_checked_and_scoped_to_project_and_spec(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);name='condition-history-'+'c'*64+'.json'
            profile={'history':[{'round':73,'source_revision':'old',
                'result_ref':{'record_id':'M73'},'status':'PASS'}]}
            write_json(root,'pipeline/condition-progress/'+name,profile)
            digest=hashlib.sha256((root/'pipeline/condition-progress'/name).read_bytes()).hexdigest()
            index={'project_id':'p','spec_revision':'a'*64,'conditions':[{
                'condition_id':'C1','history_ref':{'path':'.lab-pipeline/'+name,'sha256':digest}}]}
            write_json(root,'pipeline/condition-progress.json',index)
            self.assertEqual(display.for_pipeline(root,self.view())['conditions']['C1']['state'],'needs_retest')
            index['spec_revision']='b'*64;write_json(root,'pipeline/condition-progress.json',index)
            self.assertEqual(display.for_pipeline(root,self.view())['conditions']['C1']['state'],'untested')
            index['spec_revision']='a'*64;index['conditions'][0]['history_ref']['sha256']='0'*64
            write_json(root,'pipeline/condition-progress.json',index)
            self.assertEqual(display.for_pipeline(root,self.view())['conditions']['C1']['state'],'untested')


class VerificationDisplayAPITests(unittest.TestCase):
    def test_api_adds_history_labels_without_modifying_acceptance_or_files(self):
        from unittest.mock import patch
        from fastapi.testclient import TestClient
        import app.main as application
        from common import pipeline_memory
        with tempfile.TemporaryDirectory() as tmp:
            artifacts=Path(tmp);pid,jid='a'*14,'b'*14;root=artifacts/pid/jid
            root.mkdir(parents=True)
            view={'scope':{'project_id':pid,'spec_revision':'c'*64,'source_revision':'d'*64,'round':75},
                'status':{'stage':'coding','ready':False,'coverage':[{'condition_id':'C1','status':'untested','result_refs':[]}]},
                'catalog':{'conditions':[{'id':'C1'}]},'graph':{'tasks':[{'id':'T1','condition_ids':['C1']}]}}
            pipeline_memory.append_event(root,None,'spec',{'text':'fixture'},scope=view['scope'],producer='controller')
            write_json(root,'pipeline/public.json',view)
            name='condition-history-'+'e'*64+'.json'
            profile={'history':[{'round':n,'source_revision':str(n),'result_ref':{'record_id':'M'+str(n)},'status':s}
                for n,s in [(73,'PASS'),(74,'NOT_EXECUTED')]]}
            write_json(root,'pipeline/condition-progress/'+name,profile)
            digest=hashlib.sha256((root/'pipeline/condition-progress'/name).read_bytes()).hexdigest()
            write_json(root,'pipeline/condition-progress.json',{'project_id':pid,'spec_revision':'c'*64,
                'conditions':[{'condition_id':'C1','history_ref':{'path':'.lab-pipeline/'+name,'sha256':digest}}]})
            row={'id':jid,'project_id':pid,'status':'RUNNING','phase':'codex_development'}
            with patch.object(application,'ARTIFACTS',artifacts),patch.object(application.db,'job_row',return_value=row):
                response=TestClient(application.app).get('/api/jobs/'+jid+'/pipeline')
            self.assertEqual(response.status_code,200)
            value=response.json()
            self.assertEqual(value['status']['coverage'][0]['status'],'untested')
            self.assertFalse(value['status']['ready'])
            self.assertEqual(value['verification_display']['tasks']['T1']['state'],'needs_retest')
            self.assertEqual(value['verification_display']['tasks']['T1']['last_common_success']['round'],73)
            self.assertEqual(json.loads((root/'pipeline/public.json').read_text()),view)


if __name__=='__main__':unittest.main()
