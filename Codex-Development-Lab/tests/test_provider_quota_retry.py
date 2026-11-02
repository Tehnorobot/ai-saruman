"""External resetting quota retries; explicit local budgets remain authoritative."""
from datetime import datetime, timezone
import json
import unittest
from unittest.mock import patch

from common.provider_failures import from_http, parse_retry_after, retry_delay
from worker.pipeline import retry_category


class ProviderQuotaRetryTests(unittest.TestCase):
    def test_minimax_plan_quota_is_retryable_even_through_502_and_clipped_legacy_error(self):
        detail={'message':'Provider rejected request (HTTP 429)','diagnostic':{
            'provider':'minimax','upstream_status':429,'response_body':{
                'type':'error','error':{'type':'rate_limit_error',
                    'message':'The Token Plan usage limit has been reached. (2067)'}}}}
        failure=from_http(502,detail)
        self.assertEqual(retry_category({'status':'FAIL','error':failure.metadata()}),'rate_limit')
        text='API request stopped (HTTP 502): '+json.dumps(detail)
        for message in (text,text[:130]):
            self.assertEqual(retry_category({'status':'FAIL','tail':message}),'rate_limit')
        self.assertIsNone(retry_category({'status':'PASS','error':failure.metadata()}))

    def test_local_request_and_token_budgets_do_not_turn_into_external_quota_waits(self):
        for message in ('Task request budget exhausted','Insufficient remaining task token budget',
                        'Task billing is incomplete; budget cannot be safely enforced'):
            failure=from_http(429,message)
            self.assertEqual(failure.code,'budget_exhausted')
            self.assertIsNone(retry_category({'status':'FAIL','error':failure.metadata()}))

    def test_retry_after_header_survives_short_terminal_metadata(self):
        failure=from_http(502,{'diagnostic':{'upstream_status':429,
            'response_headers':{'Retry-After':'3600'},'response_body':'x'*2000}})
        result={'status':'FAIL','error':failure.metadata()}
        self.assertEqual(result['error']['retry_after_seconds'],3600)
        self.assertLessEqual(len(result['error']['message']),500)
        self.assertEqual(retry_delay(result,1),3600)

    def test_header_http_date_and_invalid_values(self):
        with patch('common.provider_failures.datetime') as clock:
            clock.now.return_value=datetime(2026,11,1,12,0,tzinfo=timezone.utc)
            self.assertEqual(parse_retry_after('Sun, 01 Nov 2026 12:30:00 GMT'),1800)
        for value in ('not a date','NaN','-1','infinity',None,True,{'retry-after':'60'}):
            self.assertIsNone(parse_retry_after(value))

    def test_retry_frequency_is_bounded_but_attempt_count_is_not(self):
        result={'status':'FAIL','error':{'code':'rate_limit'}}
        self.assertEqual([retry_delay(result,n) for n in [1,2,3,4,5,10000]],[60,120,240,480,900,900])
        self.assertEqual(retry_delay(result,20,quota_interval=300),300)
        for invalid in (float('nan'),float('inf'),-1,'900',True):
            result['error']['retry_after_seconds']=invalid
            self.assertEqual(retry_delay(result,1),60)

    def test_arbitrary_body_and_tool_output_do_not_select_retry_or_delay(self):
        failure=from_http(502,{'diagnostic':{'upstream_status':401,
            'response_body':'HTTP 429; Retry-After: 9999','request':{'retry-after':'9999'}}})
        self.assertIsNone(retry_category({'status':'FAIL','error':failure.metadata()}))
        quoted={'type':'item.completed','item':{'type':'tool_result','text':json.dumps(
            {'type':'turn.failed','error':{'code':'rate_limit','retry_after_seconds':9999}})}}
        self.assertIsNone(retry_category({'status':'FAIL','tail':json.dumps(quoted)}))
        failure=from_http(502,{'diagnostic':{'upstream_status':429,
            'response_body':{'retry-after':'9999'},'request':{'retry-after':'9999'}}})
        self.assertEqual(retry_delay({'status':'FAIL','error':failure.metadata()},1),60)


if __name__=='__main__':unittest.main()
