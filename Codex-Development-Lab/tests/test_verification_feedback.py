import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from common import verification_feedback as feedback


class VerificationFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ws = self.root / 'workspace'
        self.ws.mkdir()
        self.job = self.root / 'job'
        self.stage = self.job / 'round-03'
        self.scenario = self.stage / 'acceptance' / 'client-f8'
        self.scenario.mkdir(parents=True)
        self.result = {'id': 'client-f8', 'status': 'FAIL', 'test_status': 'FAIL',
                       'detail': 'Independent assertion failed', 'log': 'acceptance/client-f8/test.log'}
        self.report = {'acceptance': {'source_sha256': 'a' * 64},
                       'acceptance_results': {'scenarios': [self.result]}}

    def export(self):
        return feedback.export_feedback(self.ws, self.job, self.report, 3)

    def log(self, text):
        (self.scenario / 'test.log').write_text(text, encoding='utf-8')

    def xml(self, text):
        folder = self.scenario / 'assertions'
        folder.mkdir(exist_ok=True)
        data = text.encode('utf-8')
        (folder / 'report-000.xml').write_bytes(data)
        self.result['assertion_evidence'] = {'reports': [{
            'file': 'acceptance/client-f8/assertions/report-000.xml',
            'sha256': hashlib.sha256(data).hexdigest()}]}

    def texts(self, index):
        return '\n'.join((self.ws / item['path']).read_text(encoding='utf-8') for item in index['files'])

    def symlink(self, source, target, directory=False):
        try:
            os.symlink(source, target, target_is_directory=directory)
        except (OSError, NotImplementedError):
            self.skipTest('Symbolic links unavailable')

    def test_root_cause_before_tail_and_multiline_trace_are_preserved(self):
        trace = ('LAB_CONTROL_TRACE seq=16 WRITE HEAD key=forward requested=true observed=false\n'
                 '  at Movement.applyInputs(Movement.java:757)\n'
                 '  at MovementQueue.tick(MovementQueue.java:1287)\n')
        self.log('Caused by: specific-root-cause\n' + trace + ('normal output\n' * 15000))
        index = self.export()
        text = self.texts(index)
        self.assertIn('specific-root-cause', text)
        self.assertIn(trace.strip(), text)
        self.assertTrue(index['truncated'])
        self.assertTrue(index['files'][0]['source_line_ranges'])
        self.assertEqual(index['source_sha256'], 'a' * 64)
        self.assertIn('does not establish PASS', text)
        self.assertNotIn('status', index['files'][0])

    def test_full_small_xml_and_system_output_are_available(self):
        self.log('failure')
        xml = ('<testsuite failures="1"><testcase name="check"><failure>bad</failure></testcase>'
               '<system-out>LAB_CONTROL_TRACE key=sprint\n  at Movement.tick()</system-out></testsuite>')
        self.xml(xml)
        index = self.export()
        self.assertIn(xml, self.texts(index))
        self.assertFalse(index['files'][1]['truncated'])
        self.assertEqual(index['files'][1]['source_sha256'], hashlib.sha256(xml.encode()).hexdigest())

    def test_large_xml_extract_keeps_system_output_trace(self):
        self.log('failure')
        self.xml('<testsuite><system-out>\n' + ('ordinary\n' * 12000) +
                 'LAB_CONTROL_TRACE WRITE HEAD requested=true observed=false\n  at Named.writer()\n' +
                 '</system-out></testsuite>')
        index = self.export()
        self.assertIn('Named.writer()', self.texts(index))
        self.assertTrue(index['files'][1]['truncated'])

    def test_xml_digest_mismatch_and_dtd_are_rejected(self):
        self.log('failure')
        self.xml('<testsuite/>')
        self.result['assertion_evidence']['reports'][0]['sha256'] = '0' * 64
        self.assertEqual(len(self.export()['files']), 1)
        self.xml('<!DOCTYPE testsuite [<!ENTITY x "leak">]><testsuite/>')
        self.assertEqual(len(self.export()['files']), 1)

    def test_arbitrary_paths_are_not_opened(self):
        outside = self.root / 'secret'
        outside.write_text('PRIVATE')
        for path in ('../../secret', str(outside), 'https://example.com/log',
                     'acceptance/client-other/test.log', 'acceptance/client-f8/../../secret'):
            self.result['log'] = path
            index = self.export()
            self.assertEqual(index['files'], [])
            self.assertNotIn('PRIVATE', json.dumps(index))

    def test_source_symlink_escape_is_rejected(self):
        outside = self.root / 'outside'
        outside.write_text('PRIVATE')
        self.symlink(outside, self.scenario / 'test.log')
        index = self.export()
        self.assertEqual(index['files'], [])
        self.assertTrue(index['errors'])

    def test_source_parent_symlink_escape_is_rejected(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'test.log').write_text('PRIVATE')
        self.result['id'] = 'client-other'
        self.result['log'] = 'acceptance/client-other/test.log'
        self.symlink(outside, self.scenario.parent / 'client-other', True)
        self.assertEqual(self.export()['files'], [])

    def test_feedback_directory_symlink_fails_without_external_writes(self):
        outside = self.root / 'outside'
        outside.mkdir()
        self.symlink(outside, self.ws / '.lab-feedback', True)
        self.log('failure')
        with self.assertRaises((OSError, ValueError)):
            self.export()
        self.assertEqual(list(outside.iterdir()), [])

    def test_output_file_hardlink_never_changes_external_inode(self):
        folder = self.ws / '.lab-feedback'
        folder.mkdir()
        outside = self.root / 'outside'
        outside.write_text('UNCHANGED')
        try:
            os.link(outside, folder / 'scenario-0-log.txt')
        except OSError:
            self.skipTest('Hard links unavailable')
        self.log('diagnostic')
        self.export()
        self.assertEqual(outside.read_text(), 'UNCHANGED')
        self.assertIn('diagnostic', (folder / 'scenario-0-log.txt').read_text())

    def test_huge_log_and_many_scenarios_obey_aggregate_budget(self):
        self.log('Caused by: root\n' + ('x' * 400 + '\n') * 5000)
        self.report['acceptance_results']['scenarios'] = [dict(self.result) for _ in range(7)]
        index = self.export()
        folder = self.ws / '.lab-feedback'
        total = sum(p.stat().st_size for p in folder.iterdir() if p.is_file())
        self.assertLessEqual(total, feedback.MAX_EXPORT_BYTES)
        self.assertLessEqual(len(index['files']), 4)
        self.assertTrue(index['truncated'])
        self.assertFalse(index['files'][0]['source_hash_complete'])

    def test_regeneration_removes_old_copies_and_round_zero_is_empty(self):
        self.log('failure')
        self.assertEqual(len(self.export()['files']), 1)
        index = feedback.export_feedback(self.ws, self.job, {}, 0)
        self.assertEqual(index['files'], [])
        self.assertEqual(list((self.ws / '.lab-feedback').iterdir()), [self.ws / '.lab-feedback' / 'index.json'])
        with self.assertRaises(ValueError):
            feedback.export_feedback(self.ws, self.job, self.report, 0)

    def test_failure_after_one_megabyte_is_copied_with_honest_origin_metadata(self):
        self.log('startup\n' + ('ordinary output\n' * 90000) +
                 'Caused by: late-actionable-root-cause\n  at Scenario.execute(Scenario.java:19)\n')
        index = self.export()
        self.assertIn('late-actionable-root-cause', self.texts(index))
        item = index['files'][0]
        self.assertTrue(item['truncated'])
        self.assertFalse(item['source_hash_complete'])
        self.assertEqual(item['source_line_ranges'], [])
        self.assertEqual(len(item['source_byte_ranges']), 2)
        self.assertEqual(item['source_byte_ranges'][-1][-1], (self.scenario / 'test.log').stat().st_size)

    def test_range_limit_preserves_late_highest_priority_failure(self):
        lines = []
        for number in range(100):
            lines.extend([f'LAB_CONTROL_TRACE INFO seq={number}'] + ['ordinary ' + 'x' * 80] * 5)
        failure_line = len(lines) + 1
        lines.extend(['LAB_CONTROL_TRACE requested=true observed=false TARGET_FAILURE',
                      '  at Important.writer()'])
        lines.extend(['ordinary ' + 'x' * 80] * 1000)
        text, ranges, truncated = feedback._excerpt('\n'.join(lines), 65536)
        self.assertTrue(truncated)
        self.assertIn('TARGET_FAILURE', text)
        self.assertIn('Important.writer()', text)
        self.assertTrue(any(start <= failure_line <= end for start, end in ranges))
        self.assertLessEqual(len(ranges), 64)
        self.assertLessEqual(len(text.encode('utf-8')), 65536)

    def test_late_failed_check_and_report_have_priority_and_omissions_have_lookup(self):
        self.log('ordinary output')
        folder = self.scenario / 'assertions'
        folder.mkdir()
        reports = []
        for number in range(5):
            marker = '<failure>late XML failure</failure>' if number == 4 else ''
            raw = f'<testsuite><testcase classname="Suite" name="case{number}">{marker}</testcase></testsuite>'.encode()
            name = f'report-{number:03d}.xml'
            (folder / name).write_bytes(raw)
            reports.append({'file': f'acceptance/client-f8/assertions/{name}',
                            'sha256': hashlib.sha256(raw).hexdigest()})
        expected = [{'classname': 'Suite', 'name': f'check{i}'} for i in range(20)]
        cases = [{**item, 'status': 'FAIL' if i == 19 else 'TEST_PASSED',
                  'message': 'late failed check' if i == 19 else '',
                  'report': 'report-004.xml' if i == 19 else 'report-000.xml'}
                 for i, item in enumerate(expected)]
        self.result['assertion_evidence'] = {'expected_tests': expected, 'test_cases': cases, 'reports': reports}
        index = self.export()
        text = self.texts(index)
        self.assertIn('late failed check', text)
        self.assertIn('late XML failure', text)
        omission = index['omissions'][0]
        self.assertEqual(omission['checks'], 4)
        self.assertEqual(omission['reports'], 1)
        self.assertEqual(omission['omitted_report_indices'], [3])
        self.assertIn('acceptance_results.json', omission['checks_source'])
        self.assertIn('assertions/report-', omission['report_path_template'])
        self.assertTrue(index['truncated'])

    def test_verbose_first_failure_does_not_starve_later_scenario(self):
        self.log('Caused by: early\n' + ('ordinary output\n' * 50000))
        other = self.stage / 'acceptance' / 'client-other'
        other.mkdir()
        (other / 'test.log').write_text('Caused by: second-scenario-root-cause')
        self.report['acceptance_results']['scenarios'].append({
            **self.result, 'id': 'client-other', 'log': 'acceptance/client-other/test.log'})
        index = self.export()
        self.assertIn('second-scenario-root-cause', self.texts(index))
        self.assertEqual({item['scenario_id'] for item in index['files']}, {'client-f8', 'client-other'})

    def test_passed_scenarios_are_not_exported_or_reinterpreted(self):
        self.log('PASS marker from untrusted stdout')
        self.result.update(status='TEST_PASSED', test_status='PASS')
        index = self.export()
        self.assertEqual(index['files'], [])
        self.assertNotIn('PASS marker', json.dumps(index))


if __name__ == '__main__':
    unittest.main()
