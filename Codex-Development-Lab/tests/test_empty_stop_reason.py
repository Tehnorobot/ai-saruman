"""An empty completion reason is an interrupted stream, never a valid tool batch."""
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock

from agent import dslab
from worker.pipeline import retry_category


class EmptyStopReasonTests(unittest.TestCase):
    def test_missing_blank_and_wrong_type_reasons_keep_usage_and_are_retryable(self):
        for reason in (None,'','   ',0,False):
            with self.subTest(reason=reason):
                events=[{'type':'content_block_start','index':0,'content_block':{
                    'type':'thinking','thinking':'PRIVATE_THINKING','signature':'PRIVATE_SIGNATURE'}},
                    {'type':'content_block_start','index':1,'content_block':{
                     'type':'tool_use','id':'read-1','name':'read_file','input':{'path':'must-not-run'}}},
                    {'type':'content_block_stop','index':1},
                    {'type':'message_delta','delta':{'stop_reason':reason},'usage':{'output_tokens':17}},
                    {'type':'message_stop'}]
                response=io.BytesIO(''.join('data: '+json.dumps(e)+'\n\n' for e in events).encode());response.status=200
                connection=MagicMock();connection.getresponse.return_value=response
                with redirect_stdout(io.StringIO()) as log,self.assertRaisesRegex(RuntimeError,'empty stop_reason') as caught:
                    dslab.streamed_message({},'private-capability',connection_factory=lambda **_:connection)
                connection.close.assert_called_once()
                rows=[json.loads(s) for s in log.getvalue().splitlines()]
                self.assertTrue(any(r.get('usage',{}).get('output_tokens')==17 for r in rows))
                diagnostic=next(r['diagnostic'] for r in rows if r['type']=='provider.error')
                self.assertFalse(diagnostic['tools_executed'])
                for secret in ('PRIVATE_THINKING','PRIVATE_SIGNATURE','private-capability','must-not-run'):
                    self.assertNotIn(secret,log.getvalue())
                self.assertEqual(retry_category({'status':'FAIL','tail':str(caught.exception)}),'stream_interrupted')


if __name__=='__main__':unittest.main()
