"""A task deadline requests a final result without killing reads or losing input."""
import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from agent import dslab


class ApiSoftHandoffTests(unittest.TestCase):
    def test_elapsed_deadline_keeps_conversation_tools_and_final_result(self):
        clock = [0.0]
        requests = []
        def send(request, *args, **kwargs):
            requests.append(copy.deepcopy(request))
            if len(requests) == 1:
                clock[0] = 3601.0
                return [{'type': 'tool_use', 'id': 'observe', 'name': 'list_files',
                         'input': {}}], 'tool_use'
            return [{'type': 'text', 'text': 'Observed files; runtime remains NOT_TESTED.'}], 'end_turn'
        with tempfile.TemporaryDirectory() as root, patch.object(dslab.time, 'monotonic', side_effect=lambda:clock[0]):
            executor = dslab.Executor({'model':'synthetic','dslab_capability':'fixture',
                'execution_timeout_seconds':3600}, root, send=send)
            with redirect_stdout(io.StringIO()) as output:
                result = executor.run('Review existing sources', agent='reviewer', readonly=True)
        self.assertIn('NOT_TESTED', result)
        self.assertNotIn('SOFT HANDOFF:', requests[0]['system'][0]['text'])
        self.assertIn('SOFT HANDOFF:', requests[1]['system'][0]['text'])
        self.assertIn('without inventing evidence', requests[1]['system'][0]['text'])
        self.assertTrue(requests[1]['tools'])
        self.assertEqual(requests[1]['messages'][1]['content'][0]['id'], 'observe')
        events=[json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(sum(row['type']=='execution.handoff_requested' for row in events), 1)

    def test_repeated_runs_share_deadline_and_emit_once(self):
        with tempfile.TemporaryDirectory() as root, patch.object(dslab.time, 'monotonic', return_value=0):
            executor = dslab.Executor({'execution_soft_timeout_seconds':60}, root)
        with patch.object(dslab.time, 'monotonic', return_value=61), redirect_stdout(io.StringIO()) as output:
            first=executor.soft_handoff('planner')
            second=executor.soft_handoff('reviewer')
        self.assertEqual(first, second)
        self.assertEqual(len(output.getvalue().splitlines()), 1)

    def test_disabled_soft_deadline_does_not_request_handoff(self):
        with tempfile.TemporaryDirectory() as root, patch.object(dslab.time, 'monotonic', return_value=10000):
            executor=dslab.Executor({'execution_soft_timeout_seconds':0,
                                     'execution_timeout_seconds':1}, root)
            self.assertEqual(executor.soft_handoff('coder'), '')

    def test_large_prompt_compaction_preserves_initial_and_latest_tool_pair(self):
        prompt='original input ' * 1000
        window=dslab.ConversationWindow(prompt, limit=100)
        old=[{'role':'assistant','content':[{'type':'tool_use','id':'old','name':'read_file','input':{'path':'old'}}]},
             {'role':'user','content':[{'type':'tool_result','tool_use_id':'old','content':'old result'}]}]
        latest=[{'role':'assistant','content':[{'type':'tool_use','id':'current','name':'read_file','input':{'path':'current'}}]},
                {'role':'user','content':[{'type':'tool_result','tool_use_id':'current','content':'current exact result'}]}]
        messages=[{'role':'user','content':prompt}]+old+copy.deepcopy(latest)
        with redirect_stdout(io.StringIO()):window.compact(messages,'reviewer')
        self.assertTrue(messages[0]['content'].startswith(prompt))
        self.assertEqual(messages[-2:], latest)
        self.assertEqual(window.removed, 1)

    def test_fourth_specialist_uses_remaining_shared_budget(self):
        with tempfile.TemporaryDirectory() as root:
            executor=dslab.Executor({'dslab_max_calls':50}, root)
            executor.calls=17
            executor.delegations=3
            with patch.object(executor, 'run', return_value='Observed findings') as run, redirect_stdout(io.StringIO()):
                self.assertEqual(executor.tool('delegate',{'role':'reviewer','task':'Review the change'},'main',False),'Observed findings')
            run.assert_called_once_with('Review the change',agent='reviewer-4',readonly=True,turns=33)

    def test_configured_command_timeout_is_not_clamped_to_300_seconds(self):
        process=Mock(returncode=0,pid=12345)
        with tempfile.TemporaryDirectory() as root:
            executor=dslab.Executor({}, root)
            with patch.object(dslab.subprocess, 'Popen', return_value=process), redirect_stdout(io.StringIO()):
                executor.tool('run_command',{'argv':['python','-V'],'timeout_seconds':7200},'main',False)
        process.wait.assert_called_once_with(timeout=7200)

    def test_nonpositive_command_timeout_is_rejected_before_launch(self):
        with tempfile.TemporaryDirectory() as root:
            executor=dslab.Executor({}, root)
            with patch.object(dslab.subprocess, 'Popen') as launch:
                with self.assertRaisesRegex(ValueError,'positive'):
                    executor.tool('run_command',{'argv':['python','-V'],'timeout_seconds':0},'main',False)
            launch.assert_not_called()


if __name__=='__main__':unittest.main()
