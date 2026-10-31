import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SRC = Path(__file__).resolve().parents[1] / "storage" / "s3_cli.py"
spec = importlib.util.spec_from_file_location("s3_cli", SRC)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class FakeClient:
    def __init__(self):
        self.objects = {}
        self.calls = 0
    def upload_file(self, filename, bucket, key, ExtraArgs=None):
        self.calls += 1
        assert ExtraArgs["ContentType"] == "video/mp4"
        self.objects[(bucket, key)] = Path(filename).stat().st_size
    def head_object(self, Bucket, Key):
        return {"ContentLength": self.objects[(Bucket, Key)]}

class StorageTests(unittest.TestCase):
    def test_config_region_endpoint_and_limits(self):
        with patch.dict(os.environ, {"AWS_ACCESS_KEY_ID": "id", "AWS_SECRET_ACCESS_KEY": "secret", "YC_S3_BUCKET": "mybucket", "YC_VIDEO_MAX_SECONDS": "60"}, clear=True):
            c=m.config()
            self.assertEqual(c["region"], "ru-central1")
            self.assertEqual(c["endpoint"], "https://storage.yandexcloud.net")
        with patch.dict(os.environ, {"AWS_ACCESS_KEY_ID": "id", "AWS_SECRET_ACCESS_KEY": "secret", "YC_S3_BUCKET": "mybucket", "YC_VIDEO_MAX_SECONDS": "61"}, clear=True):
            with self.assertRaises(ValueError): m.config()

    def test_state_atomic_and_safe_source(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            dest=root/'state.json'
            m.state_save(dest,{"schema_version":1,"objects":{"x":{"key":"v.mp4"}}})
            self.assertEqual(m.state_load(dest)["objects"]["x"]["key"],"v.mp4")
            outside=Path('/etc/hosts')
            with self.assertRaises(ValueError): m.safe_source(outside,root)

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'ffmpeg required')
    def test_verified_video_upload_clips_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/'runs'; root.mkdir()
            state=Path(td)/'state'; state.mkdir()
            path=root/'test.mp4'
            subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i','color=size=64x64:rate=5:color=blue','-t','61.5','-c:v','libx264','-pix_fmt','yuv420p',str(path)],check=True,timeout=30)
            d, codec=m.probe(path)
            self.assertGreater(d,60)
            self.assertEqual(codec, 'h264')
            c={'endpoint':'https://storage.yandexcloud.net','region':'ru-central1','bucket':'fake','prefix':'lab','seconds':60,'presign':900}
            client=FakeClient()
            first=m.upload_one(path,root,state,c,client)
            self.assertEqual(first['status'],'UPLOADED')
            self.assertTrue(first['clipped'])
            self.assertLessEqual(first['seconds'],60)
            self.assertEqual(client.calls,1)
            again=m.upload_one(path,root,state,c,client)
            self.assertEqual(again['status'],'ALREADY_UPLOADED')
            self.assertEqual(client.calls,1)

if __name__ == '__main__': unittest.main()
