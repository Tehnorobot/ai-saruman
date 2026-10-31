"""Recovery completeness and bounded backup regressions, using synthetic files."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from backup import daemon as backup
from common import db


class BackupRecoveryTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.projects=self.root/'projects'
        self.artifacts=self.root/'artifacts'
        for owner,name,value in ((db,'DB',self.root/'lab.sqlite'),
                                  (backup,'PROJECTS',self.projects),
                                  (backup,'ARTIFACTS',self.artifacts),
                                  (backup,'OUTPUT',self.root/'backups')):
            context=patch.object(owner,name,value);context.start();self.addCleanup(context.stop)
        context=patch.dict(os.environ,{'LAB_PROJECTS':str(self.projects)})
        context.start();self.addCleanup(context.stop)
        self.pid,self.jid=db.create_project('Recovery','spec.md',b'# Recover a meaningful synthetic project')
        self.workspace=self.projects/self.pid/'workspace'

    def write(self,relative,data=b'content',base=None):
        path=(base or self.workspace)/relative
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(data)
        return path

    def snapshot(self):
        path=backup.create_backup(self.jid,'recovery-test')
        with tarfile.open(path) as archive:
            manifest=json.load(archive.extractfile('backup_manifest.json'))
            contents={name:archive.extractfile(name).read() for name in manifest['files']}
        return path,manifest,contents

    def test_wrapper_dependencies_mod_resources_outputs_and_evidence_survive(self):
        files={
            'gradle/wrapper/gradle-wrapper.jar':b'wrapper fixture',
            'integration-inputs/saruman.jar':b'input dependency',
            'integration-inputs/source.zip':b'input source archive',
            'src/main/resources/assets/mod/textures/gui/icon.png':b'PNG resource fixture',
            'build/libs/mod-0.2.0.jar':b'production artifact',
            'build/libs/mod-0.2.0-sources.jar':b'sources artifact',
            'build/test-results/test/TEST-core.xml':b'<testsuite tests="5"/>',
            'build/reports/tests/test/index.html':b'<html>test report</html>',
            'build/run/clientGameTest/crash-reports/crash-client.txt':b'native thread failure',
            'build/run/clientGameTest/logs/latest.log':b'client run log',
            'build/run/clientGameTest/reports/client.json':b'{"result":"failed"}',
            'run/crash-reports/crash.txt':b'development run crash',
        }
        for name,data in files.items():self.write(name,data)
        self.write('round-01/screen.png',b'actual test screenshot fixture',self.artifacts/self.pid/self.jid)
        _,manifest,contents=self.snapshot()
        for name,data in files.items():
            self.assertEqual(contents['project/workspace/'+name],data,name)
        self.assertIn('artifacts/round-01/screen.png',contents)
        self.assertIn('state/lab.sqlite3',contents)
        self.assertEqual(manifest['schema_version'],2)

    def test_disposable_trees_are_pruned_and_exclusions_are_explained(self):
        skipped={
            '.lab-cache/gradle/caches/a.jar':'cache_directory',
            '.gradle/caches/a.zip':'cache_directory',
            'build/classes/java/main/Main.class':'generated_build_output',
            'build/run/clientGameTest/saves/Test/region/a.mca':'game_runtime_data',
            'build/run/clientGameTest/assets/objects/a':'game_runtime_data',
            'build/run/clientGameTest/natives/lib.so':'game_runtime_data',
            'build/run/clientGameTest/libraries/game.jar':'game_runtime_data',
            'clip.mp4':'video_stays_local',
        }
        for name in skipped:self.write(name,b'not copied')
        _,manifest,contents=self.snapshot()
        for name,reason in skipped.items():
            arc='project/workspace/'+name
            self.assertNotIn(arc,contents)
            matches=[item for item in manifest['exclusions']
                     if arc==item['path'] or arc.startswith(item['path']+'/')]
            self.assertTrue(any(item['reason']==reason for item in matches),(arc,matches))
        self.assertFalse(manifest['complete'])
        self.assertTrue(all(set(item)<= {'path','reason','size','scope'} for item in manifest['exclusions']))

    def test_file_limits_allow_larger_input_jar_but_explain_oversized_files(self):
        self.write('src/resource.bin',b'x'*81)
        self.write('integration-inputs/allowed.jar',b'y'*100)
        self.write('integration-inputs/oversized.jar',b'z'*129)
        with patch.object(backup,'MAX_FILE_BYTES',80),patch.object(backup,'MAX_BINARY_BYTES',128):
            _,manifest,contents=self.snapshot()
        self.assertEqual(len(contents['project/workspace/integration-inputs/allowed.jar']),100)
        for name in ('project/workspace/src/resource.bin','project/workspace/integration-inputs/oversized.jar'):
            self.assertNotIn(name,contents)
            self.assertTrue(any(item['path']==name and item['reason']=='file_size_limit' for item in manifest['exclusions']))

    def test_total_payload_limit_preserves_sources_before_optional_binary_payload(self):
        self.write('src/main/java/Essential.java',b'important source')
        self.write('integration-inputs/large.jar',b'j'*100)
        with patch.object(backup,'MAX_TOTAL_BYTES',65536+64),patch.object(backup,'MAX_STATE_BYTES',65536):
            _,manifest,contents=self.snapshot()
        self.assertIn('project/workspace/src/main/java/Essential.java',contents)
        self.assertNotIn('project/workspace/integration-inputs/large.jar',contents)
        self.assertIn('state/lab.sqlite3',contents)
        self.assertLessEqual(sum(map(len,contents.values())),65536+64)
        self.assertEqual(manifest['payload_bytes'],sum(map(len,contents.values())))
        self.assertTrue(any(item['reason']=='total_size_limit' for item in manifest['exclusions']))

    def test_file_count_and_exclusion_list_are_bounded(self):
        for index in range(10):self.write(f'src/{index}.txt',b'text')
        with patch.object(backup,'MAX_FILES',4),patch.object(backup,'MAX_EXCLUSIONS',2):
            _,manifest,contents=self.snapshot()
        self.assertLessEqual(len(contents),4)
        self.assertLessEqual(len(manifest['exclusions']),2)
        self.assertGreater(manifest['exclusions_truncated'],0)
        self.assertIn('state/lab.sqlite3',contents)

    def test_oversized_state_is_explicit_not_a_partial_database(self):
        with patch.object(backup,'MAX_STATE_BYTES',1024):
            _,manifest,contents=self.snapshot()
        self.assertNotIn('state/lab.sqlite3',contents)
        self.assertTrue(any(item['path']=='state/lab.sqlite3' and item['reason']=='state_size_limit'
                            for item in manifest['exclusions']))

    def test_each_manifest_hash_and_archive_digest_match_exact_bytes(self):
        self.write('gradle/wrapper/gradle-wrapper.jar',bytes(range(256)))
        path,manifest,contents=self.snapshot()
        for name,digest in manifest['files'].items():
            self.assertEqual(hashlib.sha256(contents[name]).hexdigest(),digest,name)
        self.assertEqual(path.with_suffix(path.suffix+'.sha256').read_text().split()[0],
                         hashlib.sha256(path.read_bytes()).hexdigest())

    @unittest.skipIf(os.name=='nt','Actual link boundaries are verified on Linux')
    def test_symlink_files_and_directories_never_archive_their_targets(self):
        foreign=self.root/'foreign';foreign.mkdir()
        (foreign/'private.txt').write_bytes(b'NEVER_COPY')
        (self.workspace/'linked-file.txt').symlink_to(foreign/'private.txt')
        (self.workspace/'linked-directory').symlink_to(foreign,target_is_directory=True)
        _,manifest,contents=self.snapshot()
        self.assertFalse(any(b'NEVER_COPY' in data for data in contents.values()))
        for name in ('linked-file.txt','linked-directory'):
            self.assertTrue(any(item['path']=='project/workspace/'+name and item['reason']=='symlink'
                                for item in manifest['exclusions']))

    @unittest.skipIf(os.name=='nt','POSIX no-follow directory descriptor boundary')
    def test_directory_replaced_by_symlink_during_scan_is_not_followed(self):
        directory=self.workspace/'src';directory.mkdir()
        foreign=self.root/'foreign';foreign.mkdir()
        (foreign/'private.txt').write_bytes(b'NEVER_COPY')
        original=backup._scan_directory
        @contextmanager
        def swap(root,relative):
            if root/relative==directory:
                directory.rmdir()
                directory.symlink_to(foreign,target_is_directory=True)
            with original(root,relative) as iterator:yield iterator
        with patch.object(backup,'_scan_directory',swap):
            _,manifest,contents=self.snapshot()
        self.assertFalse(any(b'NEVER_COPY' in data for data in contents.values()))
        self.assertTrue(any(item['path']=='project/workspace/src' and item['reason']=='unreadable_directory'
                            for item in manifest['exclusions']))

    @unittest.skipIf(os.name=='nt','POSIX executable permissions')
    def test_wrapper_executable_permission_survives_snapshot(self):
        self.write('gradlew',b'#!/bin/sh\n').chmod(0o755)
        path,_,_=self.snapshot()
        with tarfile.open(path) as archive:
            self.assertEqual(stat.S_IMODE(archive.getmember('project/workspace/gradlew').mode),0o755)


if __name__=='__main__':unittest.main()
