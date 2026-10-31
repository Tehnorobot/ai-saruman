"""Original decisions survive pagination, revisions, crashes and hostile mirrors."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from common import pipeline_memory as memory


class PipelineMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.job = self.root / 'job'
        self.ws = self.root / 'workspace'
        self.job.mkdir()
        self.ws.mkdir()
        self.scope = {'project_id': 'project-a', 'spec_revision': 'a' * 64,
                      'source_revision': 'b' * 64, 'round': 1}

    def add(self, payload, **kwargs):
        options = {'scope': self.scope, 'producer': 'planner', 'task_ids': ['T1'],
                   'requirement_ids': ['R1'], 'tags': ['proposed']}
        options.update(kwargs)
        return memory.append_event(self.job, self.ws, 'plan', payload, **options)

    def test_duplicate_direct_index_references_are_rejected_before_commit(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate pipeline memory task IDs'):
            self.add({'full_original': 'kept'}, task_ids=['T1', 'T1'])
        self.assertFalse((self.job/'pipeline-memory/index.json').exists())

    def test_large_original_is_not_truncated_and_record_matches_wire_contract(self):
        text = 'решение' * 200000
        payload = {'contract': 'PlannerDecision', 'rationale': text,
                   'actions': [{'goal': 'действие ' * 2000} for _ in range(12)]}
        record = self.add(payload)
        rid = record['record_id']
        self.assertEqual(memory.read_payload(self.job, rid), payload)
        self.assertEqual(memory.load_record(self.job, rid), record)
        self.assertEqual(record['contract'], 'MemoryRecord')
        self.assertEqual(record['entry']['contract'], 'MemoryWrite')
        self.assertEqual(set(record), {'contract', 'version', 'record_id', 'created_at', 'entry', 'index_ref'})
        exported = self.ws / record['entry']['payload_ref']['path']
        self.assertEqual(json.loads(exported.read_text(encoding='utf-8')), payload)
        data = exported.read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), record['entry']['payload_ref']['sha256'])
        summary = memory.compact_summary(self.job)
        self.assertLess(len(json.dumps(summary)), 2000)
        self.assertNotIn('rationale', json.dumps(summary))

    def test_all_plans_retained_with_append_safe_pagination(self):
        ids = [self.add({'summary': f'plan {number}', 'body': f'full {number}'})['record_id']
               for number in range(73)]
        first = memory.query(self.job, page_size=7)
        self.assertEqual(len(first['records']), 7)
        self.assertEqual(first['omitted_records'], 66)
        # Appending must not shift older pages and repeat records.
        newer = self.add({'summary': 'new since pagination'})['record_id']
        seen = [item['record_id'] for item in first['records']]
        cursor = first['next_cursor']
        while cursor is not None:
            page = memory.query(self.job, cursor=cursor, page_size=7)
            seen.extend(item['record_id'] for item in page['records'])
            cursor = page['next_cursor']
        self.assertEqual(seen, list(reversed(ids)))
        self.assertNotIn(newer, seen)
        self.assertEqual(memory.compact_summary(self.job)['record_count'], 74)
        self.assertEqual(memory.read_payload(self.job, ids[0])['body'], 'full 0')

    def test_source_revisions_and_error_hypothesis_result_links_are_preserved(self):
        failure = memory.append_event(self.job, self.ws, 'verification',
            {'status': 'FAIL', 'checkpoints': [{'id': 'approach', 'status': 'PASS'},
                                            {'id': 'hit', 'status': 'FAIL'}]},
            scope=self.scope, producer='runner', tags=['verified_fail'])
        hypothesis = self.add({'hypothesis': 'H1', 'experiment': 'Measure action event'},
            links=[{'relation': 'responds_to', 'record_id': failure['record_id']}])
        changed = {**self.scope, 'source_revision': 'c' * 64, 'round': 2}
        success = memory.append_event(self.job, self.ws, 'verification', {'status': 'PASS'},
            scope=changed, producer='runner', tags=['verified_pass'],
            links=[{'relation': 'tests_hypothesis', 'record_id': hypothesis['record_id']}])
        self.assertEqual(memory.load_record(self.job, failure['record_id'])['entry']['tags'], ['verified_fail'])
        self.assertEqual(memory.load_record(self.job, failure['record_id'])['entry']['scope'], self.scope)
        self.assertEqual(memory.load_record(self.job, success['record_id'])['entry']['scope'], changed)
        self.assertEqual(memory.read_payload(self.job, failure['record_id'])['checkpoints'][0]['status'], 'PASS')
        self.assertEqual(memory.load_record(self.job, success['record_id'])['entry']['links'][0]['record_id'],
                         hypothesis['record_id'])

    def test_only_known_private_fields_removed_not_provider_metadata(self):
        payload = {'summary': 'public', 'dslab_model': 'public provider identity',
                   'minimax_api_key': 'private-provider-key', 'X-API-Key': 'private-header',
                   'nested': [{'api_key': 'secret', 'authorization': 'secret',
                               'encrypted_reasoning': 'private', 'text': 'full text'}],
                   'usage': {'input_tokens': 12, 'cache_read_tokens': 8}}
        record = self.add(payload)
        public = memory.read_payload(self.job, record['record_id'])
        self.assertEqual(public['nested'], [{'text': 'full text'}])
        self.assertEqual(public['dslab_model'], payload['dslab_model'])
        self.assertEqual(public['usage'], payload['usage'])
        self.assertNotIn('minimax_api_key', public)
        self.assertNotIn('X-API-Key', public)

    def test_tampered_workspace_is_never_ingested_and_can_be_restored(self):
        record = self.add({'summary': 'original', 'status': 'UNVERIFIED'})
        payload = self.ws / record['entry']['payload_ref']['path']
        payload.write_text('{"status":"PASS"}', encoding='utf-8')
        (self.ws / memory.EXPORT / 'index.json').write_text('{"record_count":999}', encoding='utf-8')
        self.assertEqual(memory.read_payload(self.job, record['record_id'])['status'], 'UNVERIFIED')
        self.assertEqual(memory.compact_summary(self.job)['record_count'], 1)
        self.assertFalse(memory.refresh_export(self.job, self.ws)['export_errors'])
        self.assertEqual(json.loads(payload.read_text(encoding='utf-8'))['status'], 'UNVERIFIED')

    def test_record_and_page_files_remain_unchanged_after_further_events(self):
        record = self.add({'summary': 'first'})
        filename = self.job / memory.NAME / 'records' / (record['record_id'] + '.json')
        page = self.ws / record['index_ref']['path']
        before = filename.read_bytes(), page.read_bytes()
        self.add({'summary': 'second'})
        self.assertEqual((filename.read_bytes(), page.read_bytes()), before)
        self.assertEqual(hashlib.sha256(page.read_bytes()).hexdigest(), record['index_ref']['sha256'])

    def test_public_record_write_and_query_match_runtime_contracts(self):
        from common.pipeline_contracts import validate
        record = self.add({'summary': 'contract integration'})
        validate('MemoryRecord', record)
        validate('MemoryWrite', record['entry'])
        validate('MemoryResult', memory.query(self.job))

    def test_missing_corrupt_and_stale_index_recovered_from_commit_records(self):
        first = self.add({'summary': 'first'})
        index = self.job / memory.NAME / 'index.json'
        old = index.read_bytes()
        second = self.add({'summary': 'second'})
        index.write_bytes(old)  # simulate crash before index replacement
        self.assertEqual(memory.compact_summary(self.job)['record_count'], 2)
        index.write_text('broken json', encoding='utf-8')
        self.assertEqual(memory.compact_summary(self.job)['record_count'], 2)
        index.unlink()
        third = self.add({'summary': 'third'})
        self.assertEqual(memory.compact_summary(self.job)['record_count'], 3)
        for record in (first, second, third):
            self.assertTrue(memory.read_payload(self.job, record['record_id'])['summary'])
        self.assertTrue(third['record_id'].startswith('M-0000000000000003-'))

    def test_payload_digest_failure_is_explicit_and_does_not_report_empty_history(self):
        record = self.add({'summary': 'original'})
        filename = self.job / memory.NAME / 'payloads' / (record['record_id'] + '.json')
        filename.write_text('{"summary":"tampered"}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'digest mismatch'):
            memory.read_payload(self.job, record['record_id'])
        (self.job / memory.NAME / 'index.json').unlink()
        with self.assertRaisesRegex(ValueError, 'digest mismatch'):
            memory.compact_summary(self.job)

    def test_query_filters_full_original_and_cursor_bound_to_filters(self):
        first = self.add({'summary': 'no clue in summary', 'nested': {'diagnostic': 'UNIQUE-STACK'}})
        self.add({'summary': 'different task'}, task_ids=['T2'])
        found = memory.query(self.job, query='unique-stack')
        self.assertEqual([item['record_id'] for item in found['records']], [first['record_id']])
        found = memory.query(self.job, task_ids=['T2'])
        self.assertEqual(len(found['records']), 1)
        page = memory.query(self.job, page_size=1)
        with self.assertRaisesRegex(ValueError, 'cursor'):
            memory.query(self.job, task_ids=['T2'], cursor=page['next_cursor'])
        self.assertEqual(memory.query(self.job, record_ids=[first['record_id']])['records'][0]['record_id'], first['record_id'])

    def test_parallel_writers_do_not_lose_events(self):
        with ThreadPoolExecutor(max_workers=5) as pool:
            records = list(pool.map(lambda number: self.add({'summary': f'event {number}'}), range(15)))
        self.assertEqual(len({record['record_id'] for record in records}), 15)
        self.assertEqual(memory.compact_summary(self.job)['record_count'], 15)

    def test_no_archive_has_empty_readable_summary(self):
        self.assertEqual(memory.compact_summary(self.job)['record_count'], 0)
        self.assertEqual(memory.query(self.job)['records'], [])

    def test_invalid_identifiers_scope_and_non_json_data_fail_before_record_commit(self):
        for value in ('../outside', '/root', 'M-short', 'x\\y'):
            with self.assertRaises(ValueError):
                memory.load_record(self.job, value)
        with self.assertRaises(ValueError):
            self.add({'body': 'fine'}, scope={**self.scope, 'round': True})
        with self.assertRaises(ValueError):
            self.add({'body': float('nan')})
        with self.assertRaises(ValueError):
            self.add({'body': object()})
        with self.assertRaises(ValueError):
            self.add({'body': 'fine'}, task_ids='T1')
        self.assertEqual(memory.compact_summary(self.job)['record_count'], 0)

    @unittest.skipUnless(os.name == 'posix', 'Production anchored paths run on Linux')
    def test_workspace_symlink_cannot_redirect_writes_and_original_survives(self):
        outside = self.root / 'outside'
        outside.mkdir()
        sentinel = outside / 'index.json'
        sentinel.write_text('sentinel', encoding='utf-8')
        (self.ws / '.lab-memory').symlink_to(outside, target_is_directory=True)
        record = self.add({'summary': 'original'})
        self.assertEqual(memory.read_payload(self.job, record['record_id']), {'summary': 'original'})
        self.assertEqual(sentinel.read_text(encoding='utf-8'), 'sentinel')
        self.assertTrue(memory.compact_summary(self.job)['export_errors'])

    @unittest.skipUnless(os.name == 'posix', 'Production anchored paths run on Linux')
    def test_canonical_archive_symlink_is_rejected(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (self.job / memory.NAME).symlink_to(outside, target_is_directory=True)
        with self.assertRaises((ValueError, OSError)):
            self.add({'summary': 'must not escape'})
        self.assertEqual(list(outside.iterdir()), [])

    @unittest.skipUnless(os.name == 'posix', 'Production anchored paths run on Linux')
    def test_next_append_restores_old_export_failures_after_destination_repaired(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (self.ws / '.lab-memory').symlink_to(outside, target_is_directory=True)
        first = self.add({'summary': 'first original'})
        self.assertTrue(memory.compact_summary(self.job)['export_errors'])
        (self.ws / '.lab-memory').unlink()
        self.add({'summary': 'second original'})
        restored = self.ws / first['entry']['payload_ref']['path']
        self.assertEqual(json.loads(restored.read_text(encoding='utf-8'))['summary'], 'first original')
        self.assertFalse(memory.compact_summary(self.job)['export_errors'])

    @unittest.skipUnless(os.name == 'posix', 'Production anchored paths run on Linux')
    def test_payload_file_symlinks_and_hardlinks_are_rejected(self):
        record = self.add({'summary': 'original'})
        original = self.job / memory.NAME / 'payloads' / (record['record_id'] + '.json')
        outside = self.root / 'outside.json'
        outside.write_text('{"secret":"outside"}', encoding='utf-8')
        original.unlink()
        original.symlink_to(outside)
        with self.assertRaises((ValueError, OSError)):
            memory.read_payload(self.job, record['record_id'])
        original.unlink()
        os.link(outside, original)
        with self.assertRaisesRegex(ValueError, 'hardlinked'):
            memory.read_payload(self.job, record['record_id'])


if __name__ == '__main__':
    unittest.main()
