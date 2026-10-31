"""Trusted upstream status wins over arbitrary server error-page prose."""
import json
import unittest

from worker.pipeline import retry_category


class RejectionRetryTests(unittest.TestCase):
    def test_503_html_misconfiguration_is_retryable_even_in_truncated_diagnostic(self):
        detail={'message':'Provider rejected request (HTTP 503)','diagnostic':{
            'upstream_status':503,'response_body':'Internal Server Error - Write: internal error or misconfiguration',
            'request':{'max_tokens':524288,'token_budget':1000000}}}
        text='API request stopped (HTTP 502): '+json.dumps(detail)
        for message in (text,text[:220]):
            self.assertEqual(retry_category({'status':'FAIL','tail':message}),'provider_unavailable')

    def test_nonretryable_upstream_status_is_not_hidden_by_wrapper_or_error_body(self):
        for status in (400,401,403,429,501):
            text='API request stopped (HTTP 502): '+json.dumps({
                'message':f'Provider rejected request (HTTP {status})','diagnostic':{
                    'response_body':'Provider rejected request (HTTP 503); try again'}})
            self.assertIsNone(retry_category({'status':'FAIL','tail':text}))
        self.assertIsNone(retry_category({'status':'FAIL','tail':'API request stopped (HTTP 503): Provider API key is not configured'}))
        self.assertIsNone(retry_category({'status':'FAIL','tail':'API request stopped (HTTP 400): Provider rejected request (HTTP 503)'}))

    def test_legacy_status_metadata_uses_same_rule(self):
        for status in (500,502,503,504):
            text=f'API request stopped (HTTP 502): Provider rejected request (HTTP {status})'
            self.assertEqual(retry_category({'status':'ERROR','tail':text}),'provider_unavailable')
        self.assertIsNone(retry_category({'status':'PASS','tail':text}))


if __name__=='__main__':unittest.main()
