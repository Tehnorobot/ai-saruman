"""Complete history survives the bounded prompt cache and agent workspace."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import memory_archive
from worker import round_context


class MemoryArchiveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.ws = self.root / 'workspace'
        self.job = self.root / 'job'
        self.ws.mkdir()
        self.job.mkdir()
        self.digest = hashlib.sha256(b'spec A').hexdigest()

    def report(self, number, label=None, status='FAIL', detail=None):
        folder = self.job / f'round-{number:02d}'
        folder.mkdir(exist_ok=True)
        value = {'checks': [{'label': label or f'check-{number}', 'status': status,
                             'tail': detail or f'error {number}'}]}
        (folder / 'verification_complete.json').write_text(json.dumps(value), encoding='utf-8')
        return value

    def refresh(self, n, **kwargs):
        return round_context.refresh_archive(self.ws, self.job, n, self.digest, **kwargs)

    def test_all_errors_and_rounds_survive_summary_limit_and_export_pages(self):
        for number in range(1, 112):
            self.report(number)
        memory = self.refresh(112)
        self.assertEqual(len(memory['errors']), 30)
        self.assertEqual(memory['memory_archive']['counts']['errors'], 111)
        self.assertEqual(memory['memory_archive']['omitted_errors'], 81)
        index = memory_archive.read_json(self.ws, '.lab-memory/index.json')
        self.assertEqual([p['count'] for p in index['error_pages']], [100, 11])
        errors = memory_archive.load(self.job)['errors']
        oldest = next(e for e in errors if e['first_round'] == 1)
        exported = memory_archive.read_json(self.ws, f".lab-memory/errors/{oldest['signature']}.json")
        self.assertEqual(exported['detail'], 'error 1')
        self.assertRegex(exported['source_reports'][0]['agent_path'], r'^\.lab-memory/rounds/01-[0-9a-f]{64}-verification\.json$')
        self.assertIsNotNone(memory_archive.read_json(self.ws, exported['source_reports'][0]['agent_path']))
        self.assertEqual(memory_archive.read_json(self.ws, '.lab-memory/rounds/01-verification.json')['checks'][0]['tail'], 'error 1')

    def test_migration_restores_all_snapshot_notes_and_complete_handoffs(self):
        for number in range(1, 13):
            self.report(number)
            folder = self.job / f'round-{number:02d}'
            handoff = {'spec_sha256': self.digest, 'source_round': number,
                       'summary': f'note {number}', 'decisions': ['full decision ' + 'x' * 2000] * 9}
            (folder / 'agent-handoff.json').write_text(json.dumps(handoff), encoding='utf-8')
            (folder / 'round-memory.json').write_text(json.dumps({'spec_sha256': self.digest,
                'recent_agent_notes': [{'source_round': number, 'spec_sha256': self.digest,
                    'claims': {'summary': f'legacy snapshot {number}'}}]}), encoding='utf-8')
        memory = self.refresh(13)
        self.assertEqual(memory['memory_archive']['counts']['notes'], 24)
        self.assertEqual(len(memory['recent_agent_notes']), 8)
        notes = memory_archive.load(self.job)['notes']
        full = next(item for item in notes if item['claims'].get('summary') == 'note 1')
        self.assertEqual(len(full['claims']['decisions']), 9)
        self.assertGreater(len(full['claims']['decisions'][0]), 2000)

    def test_oversized_summary_cannot_reset_archive_and_tampered_exports_rebuild(self):
        self.report(1)
        self.refresh(2)
        (self.job / 'round-memory.json').write_text('x' * (2 * 1024 * 1024), encoding='utf-8')
        error = memory_archive.load(self.job)['errors'][0]
        copy = self.ws / '.lab-memory' / 'errors' / (error['signature'] + '.json')
        copy.write_text('{"status":"RESOLVED"}', encoding='utf-8')
        (self.ws / 'round-memory.json').write_text('{"errors":[]}', encoding='utf-8')
        memory = self.refresh(2)
        self.assertEqual(memory['errors'][0]['status'], 'OPEN')
        self.assertEqual(json.loads(copy.read_text(encoding='utf-8'))['status'], 'OPEN')

    def test_new_round_only_applies_new_report_but_old_correction_replays_later_pass(self):
        self.report(1, label='build')
        self.refresh(2)
        self.report(2, label='build', status='PASS')
        original = round_context._apply_report
        with patch.object(round_context, '_apply_report', wraps=original) as applied:
            memory = self.refresh(3)
        self.assertEqual([call.args[1] for call in applied.call_args_list], [2])
        self.assertEqual(memory['errors'][0]['status'], 'RESOLVED')
        self.report(1, label='build', detail='a revised older failure')
        memory = self.refresh(3)
        self.assertTrue(all(e['status'] == 'RESOLVED' for e in memory['errors']))
        self.assertTrue(all(e['resolved_round'] == 2 for e in memory['errors']))

    def test_complete_observation_history_and_replay_counts_exceed_thirty(self):
        for number in range(1, 42):
            self.report(number, label='build', detail='same error')
        self.refresh(42)
        error = memory_archive.load(self.job)['errors'][0]
        self.assertEqual(error['repeat_count'], 41)
        self.assertEqual(error['observed_rounds'], list(range(1, 42)))
        self.report(41, label='build', detail='same error')
        self.refresh(42)
        self.assertEqual(memory_archive.load(self.job)['errors'][0]['repeat_count'], 41)

    def test_migration_rediscovers_earlier_occurrences_of_a_pruned_error(self):
        for number in range(1, 7):
            self.report(number, label='build', detail='same error')
        legacy = {'spec_sha256': self.digest, 'errors': [{
            'signature': 'a' * 64, 'label': 'build', 'detail': 'same error',
            'first_round': 6, 'last_round': 6, 'repeat_count': 1,
            'observed_rounds': [6], 'status': 'OPEN'}]}
        self.refresh(7, legacy_memory=legacy)
        error = memory_archive.load(self.job)['errors'][0]
        self.assertEqual((error['first_round'], error['last_round'], error['repeat_count']), (1, 6, 6))
        self.assertEqual(sorted(error['observed_rounds']), list(range(1, 7)))

    def test_reverification_retains_immutable_original_report_and_public_copy(self):
        original = self.report(1, detail='original failure')
        self.refresh(2)
        first = memory_archive.load(self.job)['rounds']['1']['report_revision']
        self.report(1, status='PASS')
        self.refresh(2)
        manifest = memory_archive.load(self.job)['rounds']['1']
        self.assertEqual(len(manifest['report_versions']), 2)
        error = memory_archive.load(self.job)['errors'][0]
        self.assertEqual(error['observed_rounds'], [1])
        self.assertEqual(error['repeat_count'], 1)
        self.assertEqual(error['status'], 'RESOLVED')
        raw = memory_archive.read_json(self.job, f'memory-archive/reports/01-{first}.json')
        self.assertEqual(raw, original)
        public = memory_archive.read_json(self.ws, f'.lab-memory/rounds/01-{first}-verification.json')
        self.assertEqual(public['checks'][0]['tail'], 'original failure')

    def test_spec_changes_keep_both_archives_and_restore_current_spec_errors(self):
        report = self.report(1, label='build', detail='same error')
        self.refresh(2)
        spec_a = self.digest
        self.digest = hashlib.sha256(b'spec B').hexdigest()
        self.assertEqual(self.refresh(2)['memory_archive']['current_open_errors'], 0)
        self.report(2, label='build', detail='same error')
        self.refresh(3)
        errors = memory_archive.load(self.job)['errors']
        self.assertEqual(len(errors), 2)
        self.assertEqual(len({e['signature'] for e in errors}), 2)
        self.digest = spec_a
        memory = self.refresh(3)
        self.assertEqual(len(memory['errors']), 1)
        self.assertEqual(memory['errors'][0]['first_round'], 1)

    def test_huge_notes_retained_but_prompt_bounded_and_source_unknown(self):
        (self.ws / 'agent-handoff.json').write_text(json.dumps({'summary': 's' * (1024 * 1024),
            'next_steps': ['step' * 20000] * 10,
            'errors_and_solutions': [{'error': 'e' * 100000, 'solution': 's' * 100000,
                                     'evidence': 'v' * 100000, 'api_key': 'PRIVATE'}] * 10}), encoding='utf-8')
        memory = self.refresh(2)
        self.assertLess(len(json.dumps(memory)), 20000)
        note = memory_archive.load(self.job)['notes'][0]
        self.assertEqual(len(note['claims']['summary']), 1024 * 1024)
        self.assertIsNone(note['source_round'])
        self.assertNotIn('PRIVATE', json.dumps(note))
        self.refresh(6)
        self.assertEqual(len(memory_archive.load(self.job)['notes']), 1)

    def test_recent_notes_follow_observation_order_without_inventing_origin(self):
        for number in range(1, 13):
            (self.ws / 'agent-handoff.json').write_text(json.dumps({'summary': f'note {number}'}), encoding='utf-8')
            memory = self.refresh(number + 1)
        notes = memory['recent_agent_notes']
        self.assertEqual([item['claims']['summary'] for item in notes], [f'note {n}' for n in range(5, 13)])
        self.assertTrue(all(item['source_round'] is None for item in notes))
        self.assertEqual([item['first_observed_round'] for item in notes], list(range(5, 13)))
        self.assertEqual(len(memory_archive.load(self.job)['notes']), 12)

    def test_report_over_one_megabyte_and_more_than_one_hundred_checks(self):
        report = {'checks': [{'label': f'failure-{n}', 'status': 'FAIL', 'tail': 'detail' * 3000}
                             for n in range(110)], 'api_key': 'PRIVATE'}
        folder = self.job / 'round-01'
        folder.mkdir()
        (folder / 'verification_complete.json').write_text(json.dumps(report), encoding='utf-8')
        memory = self.refresh(2)
        self.assertEqual(memory['memory_archive']['counts']['errors'], 110)
        public = memory_archive.read_json(self.ws, '.lab-memory/rounds/01-verification.json')
        self.assertEqual(len(public['checks']), 110)
        self.assertEqual(len(public['checks'][0]['tail']), 18000)
        self.assertNotIn('PRIVATE', json.dumps(public))

    @unittest.skipUnless(os.name == 'posix', 'Production anchored writes use POSIX directory descriptors')
    def test_workspace_symlink_cannot_redirect_export_and_canonical_history_survives(self):
        outside = self.root / 'outside'
        outside.mkdir()
        sentinel = outside / 'index.json'
        sentinel.write_text('sentinel', encoding='utf-8')
        (self.ws / '.lab-memory').symlink_to(outside, target_is_directory=True)
        self.report(1)
        memory = self.refresh(2)
        self.assertEqual(sentinel.read_text(encoding='utf-8'), 'sentinel')
        self.assertEqual(len(memory_archive.load(self.job)['errors']), 1)
        self.assertTrue(memory['memory_archive']['export_errors'])


if __name__ == '__main__':
    unittest.main()
