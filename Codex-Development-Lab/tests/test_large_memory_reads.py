"""API read-only agents can follow large memory links without loading full files."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from agent.dslab import Executor


class LargeMemoryReadTests(unittest.TestCase):
    def test_large_utf8_record_uses_character_offsets_and_keeps_tail_accessible(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);record=root/'.lab-memory/rounds/01-verification.json'
            record.parent.mkdir(parents=True)
            text='ж'*1200000+'LATE_DIAGNOSTIC'
            record.write_text(text,encoding='utf-8')
            executor=Executor({},root)
            with contextlib.redirect_stdout(io.StringIO()):
                first=executor.tool('read_file',{'path':str(record.relative_to(root)),'limit':12},readonly=True)
                last=executor.tool('read_file',{'path':str(record.relative_to(root)),'offset':1200000,'limit':30},readonly=True)
            self.assertIn('next_offset=12',first)
            self.assertEqual(last,'LATE_DIAGNOSTIC')

    def test_large_credential_file_cannot_use_fragment_read_bypass(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'auth.json').write_text('x'*2200000)
            with self.assertRaisesRegex(ValueError,'Credential'):
                Executor({},root).tool('read_file',{'path':'auth.json','offset':2100000},readonly=True)
