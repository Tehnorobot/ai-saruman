"""Numbered evidence must preserve code operators and exact source quotes."""
import tempfile
import unittest
from pathlib import Path
from common import coverage


class EvidenceOperatorNormalizationTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.path='src/test/java/Probe.java'
        self.source=self.root/self.path;self.source.parent.mkdir(parents=True)
        self.quote=('if (diagnostic == null || !diagnostic.contains("auto_defense")\n'
                    '                    || !diagnostic.contains("which is missing")) {')
        self.source.write_text(self.quote,encoding='utf-8')
        self.ref={'path':self.path,'start_line':1,'end_line':2,'excerpt':self.quote}

    def test_nl_tab_prefix_does_not_consume_first_pipe_of_java_or(self):
        observed='\n'.join(f'{index:6}\t{line}' for index,line in enumerate(self.quote.splitlines(),16))
        self.assertEqual(coverage.read_evidence(self.root,self.ref,observed)['excerpt'],self.quote)

    def test_colon_and_pipe_display_prefixes_preserve_following_operators(self):
        for separator in (': ', ' | '):
            observed='\n'.join(str(index)+separator+line for index,line in enumerate(self.quote.splitlines(),1))
            with self.subTest(separator=separator):
                self.assertEqual(coverage.read_evidence(self.root,self.ref,observed)['excerpt'],self.quote)

    def test_real_source_numeric_colon_or_pipe_is_not_removed_from_quote(self):
        for source in ('123: source diagnostic line', '123 | source diagnostic line',
                       '123 || source diagnostic line'):
            self.source.write_text(source,encoding='utf-8')
            ref={**self.ref,'end_line':1,'excerpt':source}
            for observed in (source,'     1\t'+source):
                with self.subTest(source=source,observed=observed):
                    self.assertEqual(coverage.read_evidence(self.root,ref,observed)['excerpt'],source)

    def test_numeric_label_cannot_become_empty_and_match_unrelated_tool_output(self):
        source='123 |                  '
        self.source.write_text(source,encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'not observed through tools'):
            coverage.read_evidence(self.root,{**self.ref,'end_line':1,'excerpt':source},'unrelated output')

    def test_different_operator_is_still_unobserved(self):
        with self.assertRaisesRegex(ValueError,'not observed through tools'):
            coverage.read_evidence(self.root,self.ref,self.quote.replace('||','|'))

    def test_exact_source_validation_precedes_numbered_observation_normalization(self):
        forged=self.quote.replace('||','|')
        with self.assertRaisesRegex(ValueError,'does not match source'):
            coverage.read_evidence(self.root,{**self.ref,'excerpt':forged},forged)
