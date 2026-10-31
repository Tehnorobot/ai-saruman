"""Optional per-check explanations cannot evict valid bounded ledger evidence."""
import copy,json,unittest
from common import acceptance,acceptance_progress as progress
from tests import test_acceptance_progress as fixture


class FailureDetailsLedgerTests(unittest.TestCase):
    setUp=fixture.AcceptanceProgressTests.setUp

    def large_result(self):
        scenario=self.plan['scenarios'][0]
        scenario['expected_tests']=[{'classname':'П'*150,'name':str(i)+'Я'*150} for i in range(64)]
        result=fixture.AcceptanceProgressTests.results(self,[scenario],status='FAIL')['scenarios'][0]
        proof=result['assertion_evidence']
        proof['reports']=[{'file':'acceptance/scenario-00/assertions/report-000.xml','sha256':'c'*64}]
        proof['failures']=[{**t,'message':'Ошибка '*110} for t in scenario['expected_tests'][:8]]
        proof['missing_tests']=copy.deepcopy(scenario['expected_tests'][8:])
        proof['test_cases']=[{**t,'status':'FAIL','message':'Ошибка '*110} for t in scenario['expected_tests']]
        result['failure_details']={'checks':[{'reason':'Причина '*1000} for _ in range(64)]}
        return scenario,result

    def test_unicode_64_check_failure_roundtrips_without_losing_original_evidence(self):
        scenario,result=self.large_result();original=copy.deepcopy(result)
        self.assertGreater(len(json.dumps(result,ensure_ascii=False).encode()),128*1024)
        progress._insert(self.ledger,scenario,result,5,self.source,progress.definition_digest(scenario))
        progress.save_progress(self.root,self.ledger)
        loaded=progress.load_progress(self.root,self.catalog,self.plan)
        stored=loaded['scenarios'][scenario['id']]['latest']['result']
        self.assertEqual(stored['status'],'FAIL');self.assertEqual(stored['test_status'],'FAIL')
        for key in ('reports','expected_tests','failures','missing_tests'):
            self.assertEqual(stored['assertion_evidence'][key],original['assertion_evidence'][key])
        self.assertNotIn('failure_details',stored)
        self.assertNotIn('test_cases',stored['assertion_evidence'])
        self.assertLessEqual(len(json.dumps(stored,ensure_ascii=False).encode()),128*1024)
        self.assertEqual(result,original)
        self.assertNotIn('last_pass',loaded['scenarios'][scenario['id']])

    def test_small_result_keeps_optional_case_facts_and_explanation(self):
        scenario=self.plan['scenarios'][0]
        result=fixture.AcceptanceProgressTests.results(self,[scenario],status='FAIL')['scenarios'][0]
        result['failure_details']={'checks':[{'status':'FAIL','reason':'No hit'}]}
        result['assertion_evidence']['test_cases']=[{**scenario['expected_tests'][0],'status':'FAIL'}]
        progress._insert(self.ledger,scenario,result,1,self.source,progress.definition_digest(scenario))
        stored=self.ledger['scenarios'][scenario['id']]['latest']['result']
        self.assertEqual(stored['failure_details'],result['failure_details'])
        self.assertEqual(stored['assertion_evidence']['test_cases'],result['assertion_evidence']['test_cases'])

    def test_oversized_original_evidence_remains_rejected_without_increasing_limit(self):
        scenario,result=self.large_result()
        result['detail']='Ж'*70000
        progress._insert(self.ledger,scenario,result,1,self.source,progress.definition_digest(scenario))
        self.assertNotIn('latest',self.ledger['scenarios'][scenario['id']])
        self.assertNotIn('last_pass',self.ledger['scenarios'][scenario['id']])

    def test_optional_trimming_does_not_weaken_incomplete_pass(self):
        scenario,result=self.large_result();result.update(status='TEST_PASSED',test_status='PASS')
        progress._insert(self.ledger,scenario,result,1,self.source,progress.definition_digest(scenario))
        self.assertNotIn('last_pass',self.ledger['scenarios'][scenario['id']])
        state=acceptance.build_state(self.catalog,self.plan,
            progress.current_results(self.ledger,self.catalog,self.plan,self.source),self.source)
        item=next(s for r in state['requirements'] for s in r['scenarios'] if s['id']==scenario['id'])
        self.assertEqual(item['status'],'NOT_TESTED')
