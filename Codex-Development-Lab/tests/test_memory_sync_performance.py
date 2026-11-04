"""A lossless memory mirror repairs bytes without replaying archive-sized writes."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import pipeline_memory as memory


class MemorySyncTests(unittest.TestCase):
    @unittest.skipUnless(os.name=='posix','Descriptor directory cache is POSIX-only')
    def test_batch_reuses_safe_directories_instead_of_traversing_per_record(self):
        for number in range(15):self.append(number)
        with patch.object(memory,'_directory',wraps=memory._directory) as directory:
            value=memory.refresh_export(self.job,self.ws)
        self.assertEqual(value['files_written'],0)
        self.assertLess(directory.call_count,30)

    @unittest.skipUnless(os.name=='posix','Directory replacement defense is POSIX-only')
    def test_parent_replaced_by_link_mid_batch_is_not_certified(self):
        for number in range(3):self.append(number)
        parent=self.ws/memory.EXPORT/'payloads'
        saved=parent.with_name('payloads.saved')
        outside=self.root/'outside';outside.mkdir()
        protected=outside/'keep.txt';protected.write_text('unchanged')
        swapped=[]
        def progress(value):
            if value['completed']==1 and not swapped:
                parent.rename(saved);parent.symlink_to(outside,target_is_directory=True);swapped.append(True)
        value=memory.refresh_export(self.job,self.ws,progress=progress)
        self.assertTrue(value['export_errors'])
        self.assertEqual(protected.read_text(),'unchanged')
        self.assertEqual(list(outside.iterdir()),[protected])
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.job = self.root / 'job'
        self.ws = self.root / 'workspace'
        self.job.mkdir(); self.ws.mkdir()
        self.scope = {'project_id': 'project-a', 'spec_revision': 'a' * 64,
                      'source_revision': 'b' * 64, 'round': 1}

    def append(self, number, workspace=True, **kwargs):
        return memory.append_event(self.job, self.ws if workspace else None, 'plan',
            {'summary': f'event {number}', 'full_original': 'unaltered original ' * 100},
            scope=self.scope, producer='planner', tags=['proposed'], **kwargs)

    def test_healthy_archive_refresh_has_no_writes_and_preserves_mtimes(self):
        for number in range(12): self.append(number)
        before = {p: p.stat().st_mtime_ns for p in self.ws.rglob('*.json')}
        with patch.object(memory, '_write', wraps=memory._write) as write:
            result = memory.refresh_export(self.job, self.ws)
        self.assertEqual(result['record_count'], 12)
        self.assertEqual(result['files_written'], 0)
        self.assertEqual(result['files_unchanged'], 12 * 3 + 1)
        self.assertEqual(write.call_count, 0)
        self.assertEqual({p: p.stat().st_mtime_ns for p in before}, before)

    def test_empty_mirror_writes_one_index_for_whole_batch(self):
        records = [self.append(number, workspace=False) for number in range(17)]
        with patch.object(memory, '_write', wraps=memory._write) as write:
            result = memory.refresh_export(self.job, self.ws)
        index_writes = [call for call in write.call_args_list
                        if call.args[1] == memory.EXPORT + '/index.json']
        self.assertEqual(len(index_writes), 1)
        self.assertEqual(result['files_written'], 17 * 3 + 1)
        self.assertEqual(result['completed'], 17)
        for record in records:
            self.assertEqual(json.loads((self.ws / record['entry']['payload_ref']['path']).read_text()),
                             memory.read_payload(self.job, record['record_id']))

    def test_only_missing_or_corrupt_bytes_are_repaired_even_with_same_size_and_mtime(self):
        records = [self.append(number) for number in range(5)]
        missing = self.ws / records[0]['entry']['payload_ref']['path']
        changed = self.ws / records[2]['entry']['payload_ref']['path']
        old = changed.stat()
        changed.write_bytes(changed.read_bytes().replace(b'event 2', b'event X'))
        os.utime(changed, ns=(old.st_atime_ns, old.st_mtime_ns))
        missing.unlink()
        with patch.object(memory, '_write', wraps=memory._write) as write:
            result = memory.refresh_export(self.job, self.ws)
        self.assertEqual(result['files_written'], 2)
        self.assertEqual({call.args[1] for call in write.call_args_list},
                         {missing.relative_to(self.ws).as_posix(), changed.relative_to(self.ws).as_posix()})
        self.assertEqual(json.loads(changed.read_text())['summary'], 'event 2')

    def test_cancelled_refresh_resumes_without_rewriting_completed_records(self):
        records = [self.append(number, workspace=False) for number in range(3)]
        def stop(value):
            if value['completed'] == 1: raise ValueError('user requested pause')
        with self.assertRaisesRegex(ValueError, 'user requested pause'):
            memory.refresh_export(self.job, self.ws, progress=stop)
        self.assertEqual(memory.compact_summary(self.job)['record_count'], 3)
        original = self.ws / records[0]['entry']['payload_ref']['path']
        first_mtime = original.stat().st_mtime_ns
        result = memory.refresh_export(self.job, self.ws)
        self.assertEqual(result['files_written'], 7)  # two remaining events and one final index
        self.assertEqual(original.stat().st_mtime_ns, first_mtime)
        self.assertFalse(result['export_errors'])
        self.assertEqual(json.loads((self.ws / memory.EXPORT / 'index.json').read_text())['record_count'], 3)

    def test_poll_exception_after_append_commit_keeps_original_for_next_append(self):
        calls = []
        class Pause(Exception): pass
        def poll():
            calls.append(1)
            if len(calls) == 2: raise Pause('stop before mirror')
        with self.assertRaises(Pause):self.append(1, poll=poll)
        index = memory.index_entries(self.job)
        self.assertEqual(len(index), 1)
        first = index[0]
        self.assertEqual(memory.read_payload(self.job, first['record_id'])['summary'], 'event 1')
        self.assertTrue(memory.compact_summary(self.job)['export_errors'])
        self.append(2)
        self.assertEqual(json.loads((self.ws / first['payload_ref']['path']).read_text())['summary'], 'event 1')
        self.assertEqual(memory.compact_summary(self.job)['record_count'], 2)
        self.assertFalse(memory.compact_summary(self.job)['export_errors'])

    def test_append_failure_recovery_also_publishes_only_one_index(self):
        self.append(1, workspace=False)
        # Persist an operational export error as a previous interrupted export would.
        index_path = self.job / memory.NAME / 'index.json'
        index = json.loads(index_path.read_text()); index['export_errors'] = ['previous interrupted export']
        index_path.write_text(json.dumps(index))
        with patch.object(memory, '_write', wraps=memory._write) as write:self.append(2)
        self.assertEqual(sum(call.args[1] == memory.EXPORT + '/index.json' for call in write.call_args_list), 1)
        self.assertEqual(json.loads((self.ws / memory.EXPORT / 'index.json').read_text())['record_count'], 2)

    def test_on_commit_sees_durable_record_before_mirror_pause_and_runs_once(self):
        committed = []
        class Pause(Exception):pass
        def on_commit(record):
            canonical_index = json.loads((self.job / memory.NAME / 'index.json').read_text())
            self.assertEqual(canonical_index['records'][-1]['record_id'], record['record_id'])
            self.assertEqual(memory.read_payload(self.job, record['record_id'])['summary'], 'event 1')
            self.assertFalse((self.ws / record['entry']['payload_ref']['path']).exists())
            committed.append(record)
        def poll():
            if committed:raise Pause('pause after durable commit')
        with self.assertRaisesRegex(Pause, 'durable commit'):
            self.append(1, poll=poll, on_commit=on_commit)
        self.assertEqual(len(committed), 1)
        record = committed[0]
        self.assertEqual(memory.load_record(self.job, record['record_id']), record)
        # Reconstructing the mirror after a restart does not replay the callback
        # or create a duplicate event, and makes the original payload readable.
        memory.refresh_export(self.job, self.ws)
        self.assertEqual(len(committed), 1)
        self.assertEqual(memory.compact_summary(self.job)['record_count'], 1)
        self.assertEqual(json.loads((self.ws / record['entry']['payload_ref']['path']).read_text())['summary'], 'event 1')

    def test_on_commit_exception_propagates_and_prevents_mirror_io(self):
        seen = []
        def stop(record):
            seen.append(record['record_id'])
            raise ValueError('state callback requested stop')
        with self.assertRaisesRegex(ValueError, 'state callback requested stop'):
            self.append(1, on_commit=stop)
        self.assertEqual(len(seen), 1)
        self.assertFalse((self.ws / memory.EXPORT / 'index.json').exists())
        self.assertEqual(memory.read_payload(self.job, seen[0])['summary'], 'event 1')
        self.assertTrue(memory.compact_summary(self.job)['export_errors'])
        self.append(2)
        first = memory.load_record(self.job, seen[0])
        self.assertTrue((self.ws / first['entry']['payload_ref']['path']).exists())

    def test_progress_callback_exception_is_not_misreported_as_io_warning(self):
        self.append(1, workspace=False)
        def fail(_):raise OSError('callback failed')
        with self.assertRaisesRegex(OSError, 'callback failed'):
            memory.refresh_export(self.job, self.ws, progress=fail)
        self.assertEqual(memory.compact_summary(self.job)['record_count'], 1)

    def test_canonical_corruption_does_not_replace_a_valid_mirror(self):
        record = self.append(1)
        mirror = self.ws / record['entry']['payload_ref']['path']
        before = mirror.read_bytes()
        canonical = self.job / memory.NAME / 'payloads' / (record['record_id'] + '.json')
        canonical.write_text('{"summary":"untrusted mutation"}')
        result = memory.refresh_export(self.job, self.ws)
        self.assertTrue(result['export_errors'])
        self.assertEqual(mirror.read_bytes(), before)

    @unittest.skipUnless(os.name == 'posix', 'Anchored production mirror paths run on Linux')
    def test_linked_mirror_leaf_is_replaced_without_mutating_link_target(self):
        record = self.append(1)
        mirror = self.ws / record['entry']['payload_ref']['path']
        original = mirror.read_bytes()
        for hardlink in (False, True):
            with self.subTest(hardlink=hardlink):
                outside = self.root / ('hardlink-target.json' if hardlink else 'symlink-target.json')
                outside.write_bytes(original)
                mirror.unlink()
                if hardlink:os.link(outside, mirror)
                else:mirror.symlink_to(outside)
                result = memory.refresh_export(self.job, self.ws)
                self.assertFalse(result['export_errors'])
                self.assertEqual(result['files_written'], 1)
                self.assertFalse(mirror.is_symlink())
                self.assertEqual(mirror.stat().st_nlink, 1)
                self.assertEqual(outside.read_bytes(), original)
                self.assertNotEqual(outside.stat().st_ino, mirror.stat().st_ino)

    @unittest.skipUnless(os.name == 'posix', 'Production uses advisory flock')
    def test_pause_can_interrupt_waiting_for_another_memory_lock(self):
        self.append(1)
        class Pause(Exception):pass
        polls = []
        def poll():
            polls.append(1)
            if len(polls) >= 2:raise Pause('pause while lock busy')
        with memory._lock(self.job):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(memory.refresh_export, self.job, self.ws, poll=poll)
                with self.assertRaisesRegex(Pause, 'lock busy'):future.result(timeout=3)
        self.assertEqual(memory.compact_summary(self.job)['record_count'], 1)


if __name__ == '__main__':unittest.main()
