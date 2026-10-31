"""Planner evidence must be read, not inferred from compact memory. No paid calls."""
import copy,io,json,tempfile,unittest
from contextlib import redirect_stdout
from pathlib import Path
from agent import dslab
from common import coverage


class PlannerDiagnosticReadTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        folder=self.root/'.lab-feedback';folder.mkdir()
        self.path='.lab-feedback/scenario-0-log.txt'
        self.quote='AssertionError: Manual mouse input did not move the camera'
        (self.root/self.path).write_text(self.quote,encoding='utf-8')
        (folder/'index.json').write_text(json.dumps({'files':[{'path':self.path}]}),encoding='utf-8')
        self.task={'role':'planner','planner_evidence_required':True,'model':'fixture',
                   'provider':'litellm','dslab_capability':'fixture','dslab_max_calls':10}
        self.plan={'action':{'kind':'repair_scenario','target':'f8','evidence_refs':[
            {'path':self.path,'start_line':1,'end_line':1,'excerpt':self.quote}]}}

    def final(self,refs=True):
        value=copy.deepcopy(self.plan)
        if not refs:value['action'].pop('evidence_refs')
        return ([{'type':'text','text':json.dumps(value)}],'end_turn')

    def read(self,path=None,**args):
        return ([{'type':'tool_use','id':'read-'+str(len(self.requests)), 'name':'read_lines',
                   'input':{'path':path or self.path,'start_line':1,**args}}],'tool_use')

    def run_loop(self,answers,**kwargs):
        self.requests=[]
        iterator=iter(answers)
        def send(payload,*args,**kw):
            self.requests.append(copy.deepcopy(payload))
            result=next(iterator)
            return result() if callable(result) else result
        output=io.StringIO()
        executor=dslab.Executor(self.task,self.root,send=send)
        with redirect_stdout(output):
            value=executor.run('Plan next repair from actual diagnostics',agent=kwargs.pop('agent','planner'),
                               readonly=kwargs.pop('readonly',True),**kwargs)
        return value,output.getvalue()

    def test_successful_read_and_exact_citation_are_available_to_controller(self):
        value,events=self.run_loop([self.read,self.final])
        self.assertEqual(len(self.requests),2)
        self.assertEqual(self.requests[0]['tool_choice'],{'type':'tool','name':'read_lines'})
        self.assertNotIn('tool_choice',self.requests[1])
        logfile=self.root/'events.jsonl';logfile.write_text(events,encoding='utf-8')
        ref=json.loads(value)['action']['evidence_refs'][0]
        self.assertEqual(coverage.read_evidence(self.root,ref,coverage.observed_tool_text(logfile))['excerpt'],self.quote)

    def test_premature_final_receives_one_reminder_then_can_read(self):
        self.run_loop([self.final,self.read,self.final])
        self.assertEqual(len(self.requests),3)
        self.assertIn('tool_choice',self.requests[1])
        self.assertIn('This final answer is rejected',str(self.requests[1]['messages']))

    def test_repeated_premature_finals_can_be_corrected_within_budget(self):
        value,_=self.run_loop([self.final,self.final,self.read,self.final])
        self.assertEqual(json.loads(value),self.plan)
        self.assertEqual(len(self.requests),4)

    def test_index_empty_failed_and_unrelated_reads_do_not_satisfy_evidence_gate(self):
        for read in (lambda:self.read('.lab-feedback/index.json'),
                     lambda:self.read(start_line=999),
                     lambda:self.read('.lab-feedback/missing.txt'),
                     lambda:self.read('other.txt')):
            with self.subTest(read=read):
                self.run_loop([read,self.final,self.final,self.read,self.final])
            self.assertTrue(all('tool_choice' in r for r in self.requests[:-1]))

    def test_search_without_read_does_not_satisfy_gate(self):
        search=([{'type':'tool_use','id':'search','name':'search_text',
                  'input':{'path':self.path,'text':'not present'}}],'tool_use')
        self.run_loop([search,self.final,self.final,self.read,self.final])
        self.assertTrue(all('tool_choice' in r for r in self.requests[:-1]))

    def test_missing_refs_after_read_can_be_corrected_without_fabricated_citations(self):
        value,_=self.run_loop([self.read,lambda:self.final(False),lambda:self.final(False),self.final])
        self.assertEqual(json.loads(value),self.plan)
        self.assertEqual(len(self.requests),4)

    def test_coder_and_readonly_delegates_are_not_forced_to_read_planner_diagnostics(self):
        for agent,readonly in [('main',False),('reviewer-1',True)]:
            with self.subTest(agent=agent):
                self.run_loop([self.final],agent=agent,readonly=readonly)
                self.assertNotIn('tool_choice',self.requests[0])

    def test_existing_request_budget_still_stops_the_inline_reminder(self):
        self.task['dslab_max_calls']=1
        with self.assertRaisesRegex(RuntimeError,'request limit'):
            self.run_loop([self.final,self.read,self.final])
        self.assertEqual(len(self.requests),1)
