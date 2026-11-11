"""Storage preserves operator-sized recordings; no real S3 calls or credentials."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from common import db, recording_settings
from storage import s3_cli as storage


class RecordedUpload:
    def __init__(self):
        self.objects = {}

    def upload_file(self, path, bucket, key, **kwargs):
        self.objects[(bucket, key)] = {'bytes': Path(path).read_bytes(), 'kwargs': kwargs}

    def head_object(self, Bucket, Key):
        return {'ContentLength': len(self.objects[(Bucket, Key)]['bytes'])}


class StorageDurationPolicyTests(unittest.TestCase):
    def configuration(self, **env):
        with patch.dict(os.environ, {'AWS_ACCESS_KEY_ID': 'synthetic-id',
                'AWS_SECRET_ACCESS_KEY': 'synthetic-secret', 'YC_S3_BUCKET': 'local-test-only', **env}, clear=True):
            return storage.config()

    def test_config_ignores_removed_storage_limit_and_keeps_stable_upload_policy(self):
        baseline = self.configuration()
        self.assertIsNone(baseline['seconds'])
        self.assertEqual(storage.upload_policy(baseline)['processing_version'], 2)
        for old in ('60', '1', '3600', 'not-a-number'):
            self.assertEqual(storage.upload_policy(self.configuration(YC_VIDEO_MAX_SECONDS=old)),
                             storage.upload_policy(baseline))

    def test_probe_accepts_long_finite_duration_but_rejects_nonfinite(self):
        for duration in ('100000.5', 'Infinity', 'NaN', '0', '-1'):
            output = json.dumps({'format': {'duration': duration},
                                 'streams': [{'codec_type': 'video', 'codec_name': 'h264'}]})
            with self.subTest(duration=duration), patch.object(storage.subprocess, 'run',
                    return_value=subprocess.CompletedProcess([], 0, output, '')):
                if duration == '100000.5':
                    self.assertEqual(storage.probe(Path('clip.mp4')), (100000.5, 'h264'))
                else:
                    with self.assertRaises(ValueError):
                        storage.probe(Path('clip.mp4'))

    def test_codec_conversion_without_limit_keeps_duration_and_scales_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(storage.subprocess, 'run') as command, \
                    patch.object(storage, 'probe', return_value=(1000.0, 'h264')):
                _, seconds, clipped = storage.prepare_video(root / 'source.mp4', 1000.0, 'mpeg4', None, root)
            argv = command.call_args.args[0]
            self.assertNotIn('-t', argv)
            self.assertGreaterEqual(command.call_args.kwargs['timeout'], 4060)
            self.assertEqual(seconds, 1000.0)
            self.assertFalse(clipped)


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'ffmpeg required')
class RealStorageDurationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.runs = self.root / 'runs'
        self.runs.mkdir()
        self.source = self.runs / 'recording.mp4'
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi',
            '-i', 'color=size=64x64:rate=5:color=blue', '-t', '71', '-c:v', 'libx264',
            '-pix_fmt', 'yuv420p', str(self.source)], check=True, timeout=30)
        self.conf = {'endpoint': 'https://example.invalid', 'region': 'test-region',
                     'bucket': 'test-only', 'prefix': 'local', 'seconds': None, 'presign': 900}
        self.client = RecordedUpload()

    def test_upload_keeps_entire_over_minute_h264_and_original_hash(self):
        original = self.source.read_bytes()
        duration, _ = storage.probe(self.source)
        result = storage.upload_one(self.source, self.runs, self.root / 'state', self.conf, self.client)
        uploaded = self.client.objects[(self.conf['bucket'], result['key'])]['bytes']
        self.assertGreater(result['seconds'], 60)
        self.assertAlmostEqual(result['seconds'], duration, places=3)
        self.assertEqual(uploaded, original)
        self.assertEqual(result['sha256'], hashlib.sha256(original).hexdigest())
        self.assertFalse(result['clipped'])

    def test_future_recording_setting_does_not_retrim_completed_clip(self):
        state = self.root / 'state'
        with patch.object(db, 'DB', self.root / 'settings.sqlite3'):
            recording_settings.set_max_seconds(300)
            first = storage.upload_one(self.source, self.runs, state, self.conf, self.client)
            # The operator changes the real persisted capture preference.
            # Existing completed recordings remain the same upload object.
            recording_settings.set_max_seconds(10)
            again = storage.upload_one(self.source, self.runs, state, self.conf, self.client)
        self.assertEqual(again['status'], 'ALREADY_UPLOADED')
        self.assertEqual(again['key'], first['key'])
        self.assertEqual(len(self.client.objects), 1)
        self.assertGreater(again['seconds'], 60)

    def test_explicit_legacy_bound_above_minute_is_honored_without_58_second_clamp(self):
        output = self.root / 'converted'
        output.mkdir()
        duration, codec = storage.probe(self.source)
        path, delivered, clipped = storage.prepare_video(self.source, duration, codec, 65, output)
        self.assertTrue(clipped)
        self.assertGreater(delivered, 60)
        self.assertLessEqual(delivered, 65)
        self.assertEqual(storage.probe(path)[1], 'h264')


if __name__ == '__main__':
    unittest.main()
