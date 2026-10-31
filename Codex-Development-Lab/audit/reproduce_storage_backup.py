"""Offline audit reproductions; uses only synthetic data and standard library.

Run from the repository root with Python 3.10+. No real S3 or credentials.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backup import daemon as backup
from common import db
from storage import s3_cli as s3


def reproduce_backup_manifest_race(root):
    projects = root / "projects"
    with patch.object(db, "DB", root / "state" / "lab.sqlite3"), patch.dict(
        os.environ, {"LAB_PROJECTS": str(projects)}
    ), patch.object(backup, "PROJECTS", projects), patch.object(
        backup, "OUTPUT", root / "backups"
    ), patch.object(backup, "ARTIFACTS", root / "artifacts"):
        pid, jid = db.create_project("Synthetic audit", "spec.md", b"# Synthetic specification only")
        source = projects / pid / "workspace" / "module.py"
        source.write_bytes(b"version = 1\n")
        original_add = tarfile.TarFile.add

        def concurrent_edit(archive, path, *args, **kwargs):
            original_add(archive, path, *args, **kwargs)
            if Path(path) == source:
                # Simulate a new agent round writing after archiving, before hashing.
                source.write_bytes(b"version = 2\n")

        with patch.object(tarfile.TarFile, "add", concurrent_edit):
            result = backup.create_backup(jid, "audit")
        with tarfile.open(result) as archive:
            name = "project/workspace/module.py"
            content = archive.extractfile(name).read()
            manifest = json.load(archive.extractfile("backup_manifest.json"))
        actual = hashlib.sha256(content).hexdigest()
        recorded = manifest["files"][name]
        return {"archived_content": content.decode().strip(),
                "current_content": source.read_text().strip(),
                "manifest_matches_archive": actual == recorded}


def reproduce_s3_configuration_change(root):
    videos = root / "videos"
    videos.mkdir()
    source = videos / "example.mp4"
    source.write_bytes(b"Synthetic video bytes; ffprobe is mocked, no real media")
    configuration = {"bucket": "old-bucket", "prefix": "old-prefix", "seconds": 60}

    class FakeS3:
        def __init__(self):
            self.calls = []

        def upload_file(self, filename, bucket, key, **kwargs):
            self.calls.append((bucket, key))

        def head_object(self, **kwargs):
            return {"ContentLength": source.stat().st_size}

    client = FakeS3()
    with patch.object(s3, "probe", return_value=(1.0, "h264")):
        first = s3.upload_one(source, videos, root / "s3-state", configuration, client)
        configuration = {**configuration, "bucket": "new-bucket", "prefix": "new-prefix"}
        second = s3.upload_one(source, videos, root / "s3-state", configuration, client)
    return {"initial_status": first["status"], "after_config_change": second["status"],
            "returned_bucket": second["bucket"], "returned_key": second["key"],
            "upload_calls": client.calls}


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="lab-audit-") as temporary:
        root = Path(temporary)
        print(json.dumps({"backup_manifest_race": reproduce_backup_manifest_race(root),
                          "s3_configuration_change": reproduce_s3_configuration_change(root)},
                         indent=2))
