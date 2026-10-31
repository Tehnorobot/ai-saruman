"""A truncated response is not a plan, a tool invocation, or permission to retry."""
import tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from agent import dslab


class ApiOutputTruncationTests(unittest.TestCase):
    def test_truncated_thinking_does_not_trigger_a_second_request(self):
        with tempfile.TemporaryDirectory() as temporary:
            task={'provider':'minimax','model':'MiniMax-M3.1-Flash','reasoning_effort':'max',
                'dslab_capability':'fixture','dslab_max_output_tokens':32192,'dslab_max_calls':20}
            send=Mock(return_value=([{'type':'thinking','thinking':'unfinished thinking'}],'max_tokens'))
            executor=dslab.Executor(task,Path(temporary),send=send)
            with patch.object(dslab,'item'),patch.object(dslab,'event'), \
                    self.assertRaisesRegex(RuntimeError,'max_tokens=32192.*includes thinking'):
                executor.run('Plan',agent='planner',readonly=True,turns=20)
            send.assert_called_once()

    def test_truncated_tool_calls_cannot_edit_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            task={'provider':'minimax','model':'MiniMax-M3.1-Flash','dslab_capability':'fixture',
                  'dslab_max_output_tokens':8192,'dslab_max_calls':20}
            send=Mock(return_value=([{'type':'tool_use','id':'call','name':'write_file',
                'input':{'path':'untrusted.java','content':'should not execute'}}],'max_tokens'))
            executor=dslab.Executor(task,root,send=send)
            with patch.object(dslab,'item'),patch.object(dslab,'event'), \
                    self.assertRaisesRegex(RuntimeError,'truncated tool calls were not executed'):
                executor.run('Implement',turns=20)
            send.assert_called_once()
            self.assertFalse((root/'untrusted.java').exists())
