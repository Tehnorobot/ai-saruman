"""Standalone MiniMax probe: synthetic credentials/responses only, no API calls."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

try:
    import test_minimax as probe
except ModuleNotFoundError as exc:
    if exc.name!='test_minimax':raise
    probe=None


def wire(events):
    return io.BytesIO(''.join('data: '+json.dumps(e)+'\n\n' for e in events).encode())


@unittest.skipIf(probe is None,'Standalone root probe is outside the service image')
class MiniMaxProbeTests(unittest.TestCase):
    def test_preview_does_not_read_credentials_or_call_network(self):
        with (patch.object(probe,'env_config',side_effect=AssertionError('Credential read')),
              patch.object(probe,'invoke',side_effect=AssertionError('Network call'))):
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(probe.main([]),0)
        self.assertIn('Requests: 15',output.getvalue())

    def test_env_parser_only_returns_minimax_fields_and_never_evaluates_shell(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'fixture.env'
            path.write_text('OTHER_SECRET=ignored\nexport MINIMAX_API_KEY="synthetic-key" # note\n'
                            'MINIMAX_BASE_URL=https://api.minimax.io/anthropic # note\n',encoding='utf-8')
            with patch.dict(probe.os.environ,{'MINIMAX_API_KEY':'override-fixture','MINIMAX_BASE_URL':''}):
                result=probe.env_config(path)
        self.assertEqual(result,{'MINIMAX_API_KEY':'override-fixture','MINIMAX_BASE_URL':probe.BASE})

    def test_native_tool_fragments_usage_and_stop_are_retained(self):
        value={'java_source':'public class Selector {}','explanation':'fixture','proposed_tests':[]}
        serialized=json.dumps(value)
        events=[{'type':'message_start','message':{'usage':{'input_tokens':31,'cache_read_input_tokens':7}}},
                {'type':'content_block_start','index':0,'content_block':{'type':'thinking','thinking':'not saved'}},
                {'type':'content_block_delta','index':0,'delta':{'type':'thinking_delta','thinking':'also not saved'}},
                {'type':'content_block_start','index':1,'content_block':{'type':'tool_use','name':'submit_result','input':{}}},
                *[{'type':'content_block_delta','index':1,'delta':{'type':'input_json_delta','partial_json':part}}
                  for part in (serialized[:15],serialized[15:])],
                {'type':'message_delta','delta':{'stop_reason':'tool_use'},'usage':{'output_tokens':18}},
                {'type':'message_stop'}]
        result=probe.collect_events(wire(events),show=False)
        self.assertEqual(result['tool_calls'][0]['input'],value)
        self.assertEqual(result['usage']['cache_read_input_tokens'],7)
        self.assertTrue(all(probe.score('coder',result)['checks'].values()))
        self.assertNotIn('not saved',json.dumps(result))

    def test_partial_usage_survives_stream_exception(self):
        def interrupted():
            yield b'data: {"type":"message_start","message":{"usage":{"input_tokens":99}}}\n'
            yield b'\n'
            raise OSError('synthetic interruption')
        result=probe.collect_events(interrupted(),show=False)
        self.assertEqual(result['usage']['input_tokens'],99)
        self.assertFalse(result['stream_complete'])
        self.assertTrue(result['stream_error'])

    def test_truncation_does_not_become_success_even_with_valid_json(self):
        result={'tool_calls':[{'name':'submit_result','input':{'java_source':'class Selector {}','explanation':'fixture','proposed_tests':[]}}],
                'stream_complete':True,'stop_reason':'max_tokens'}
        score=probe.score('coder',result)
        self.assertTrue(score['checks']['schema_valid'])
        self.assertFalse(score['checks']['completed_response'])
        self.assertNotIn('code_verification',score)

    def test_plain_json_is_separate_from_native_tool_compliance(self):
        result={'text':json.dumps({'java_source':'class Selector {}','explanation':'fixture','proposed_tests':[]}),
                'stream_complete':True,'stop_reason':'end_turn'}
        score=probe.score('coder',result)
        self.assertTrue(score['checks']['schema_valid'])
        self.assertFalse(score['checks']['native_submit_result'])
        self.assertEqual(score['code_verification']['status'],'NOT_EXECUTED')

    def test_java_check_is_only_in_container_without_network_or_host_mounts(self):
        stdout='\n'.join('CASE '+str(n)+' '+(expected if expected is not None else '<null>')
                         for n,(_,_,_,expected) in enumerate(probe.CASES))
        completed=subprocess.CompletedProcess([],0,json.dumps({'phase':'run','returncode':0,'stdout':stdout,'stderr':''}),'')
        cleanup=subprocess.CompletedProcess([],0,'','')
        with patch.object(probe.subprocess,'run',side_effect=[completed,cleanup]) as run:
            result=probe.verify_code('public class Selector {}','fixture-docker','fixture-image')
        command=run.call_args_list[0].args[0]
        self.assertIn('--read-only',command);self.assertIn('no-new-privileges',command)
        self.assertEqual(command[command.index('--network')+1],'none')
        self.assertNotIn('--mount',command);self.assertNotIn('-v',command)
        self.assertEqual(result['passed'],14)
        self.assertEqual(run.call_args_list[1].args[0][1:3],['rm','-f'])

    def test_compile_error_is_not_a_behavioral_pass(self):
        failed=subprocess.CompletedProcess([],0,json.dumps({'phase':'compile','returncode':1,'stderr':'fixture compiler diagnostic'}),'')
        with patch.object(probe.subprocess,'run',side_effect=[failed,subprocess.CompletedProcess([],0,'','')]):
            result=probe.verify_code('bad source','fixture-docker','fixture-image')
        self.assertEqual(result['status'],'COMPILE_FAILED')
        self.assertNotIn('passed',result)

    def test_report_escapes_model_html_and_preserves_complete_text(self):
        payload='<script>alert(1)</script>'+'complete '*4000
        report={'results':[{'role':'reviewer','effort':'low','repeat':1,'elapsed_seconds':1,
                           'response':{'text':payload,'usage':{}},'evaluation':{'checks':{}}}]}
        with tempfile.TemporaryDirectory() as temp:
            probe.save_report(Path(temp),report)
            page=(Path(temp)/'results.html').read_text(encoding='utf-8')
            saved=json.loads((Path(temp)/'results.json').read_text(encoding='utf-8'))
        self.assertNotIn('<script>',page)
        self.assertIn('&lt;script&gt;',page)
        self.assertEqual(saved['results'][0]['response']['text'],payload)

    def test_fake_matrix_saves_answer_without_key_and_does_not_touch_lab(self):
        response={'http_status':200,'stream_complete':True,'stop_reason':'tool_use','usage':{'input_tokens':12},
                  'tool_calls':[{'name':'submit_result','input':{'java_source':'class Selector {}','explanation':'synthetic-key','proposed_tests':[]}}]}
        with (tempfile.TemporaryDirectory() as temp,patch.object(probe,'env_config',return_value={'MINIMAX_API_KEY':'synthetic-key'}),
              patch.object(probe,'invoke',return_value=response) as invoke,redirect_stdout(io.StringIO()) as output):
            folder=Path(temp)/'result'
            self.assertEqual(probe.main(['--run','--roles','coder','--efforts','low','--output-dir',str(folder)]),0)
            saved=(folder/'results.json').read_text(encoding='utf-8')
            self.assertTrue((folder/'results.html').is_file())
        self.assertEqual(invoke.call_count,1)
        self.assertNotIn('synthetic-key',saved)
        self.assertNotIn('synthetic-key',output.getvalue())

    def test_java_failure_after_printing_results_is_not_success(self):
        stdout='\n'.join('CASE '+str(n)+' '+(expected if expected is not None else '<null>')
                         for n,(_,_,_,expected) in enumerate(probe.CASES))
        failed=subprocess.CompletedProcess([],0,json.dumps({'phase':'run','returncode':1,'stdout':stdout,'stderr':'Exception'}),'')
        with patch.object(probe.subprocess,'run',side_effect=[failed,subprocess.CompletedProcess([],0,'','')]):
            result=probe.verify_code('fixture source','fixture-docker','fixture-image')
        self.assertEqual(result['status'],'RUNTIME_FAILED')
        self.assertEqual(result['passed'],0)


if __name__=='__main__':unittest.main()
