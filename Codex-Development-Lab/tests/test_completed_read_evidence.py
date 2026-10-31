"""Observed stdout is not a command success verdict."""
import json,tempfile,unittest
from pathlib import Path
from common import coverage


class CompletedReadEvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.path='src/test/java/Example.java'
        source=self.root/self.path;source.parent.mkdir(parents=True)
        self.quote='DistributionOrigins.requireInstalledFile(modsProperty, origin);'
        source.write_text(self.quote,encoding='utf-8')
        self.ref={'path':self.path,'start_line':1,'end_line':1,'excerpt':self.quote}
        self.log=self.root/'codex.jsonl'

    def write(self,event_type='item.completed',exit_code=1):
        self.event={'type':event_type,'item':{'type':'command_execution','exit_code':exit_code,
                    'command':'nl -ba src/test/java/Example.java; rg missing_pattern other.java',
                    'aggregated_output':'     1\t'+self.quote}}
        self.log.write_text(json.dumps(self.event),encoding='utf-8')

    def test_completed_nonzero_stdout_proves_reading_but_not_success(self):
        self.write()
        observed=coverage.observed_tool_text(self.log)
        self.assertEqual(coverage.read_evidence(self.root,self.ref,observed)['excerpt'],self.quote)
        self.assertEqual(self.event['item']['exit_code'],1)

    def test_running_or_unknown_completion_cannot_prove_reading(self):
        for event_type,code in [('item.started',0),('item.completed',None),('item.completed',True),('item.completed','0')]:
            self.write(event_type,code)
            with self.subTest(event_type=event_type,code=code),self.assertRaisesRegex(ValueError,'not observed'):
                coverage.read_evidence(self.root,self.ref,coverage.observed_tool_text(self.log))

    def test_failed_command_cannot_authorize_fabricated_source_quote(self):
        self.write();ref={**self.ref,'excerpt':'Invented.assertionThatDoesNotExist();'}
        with self.assertRaisesRegex(ValueError,'does not match source'):
            coverage.read_evidence(self.root,ref,coverage.observed_tool_text(self.log))
