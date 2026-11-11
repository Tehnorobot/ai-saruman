"""Synthetic command execution exercises the trusted pipeline, never Minecraft."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from worker import verification_plan as plan


FAKE_GRADLE = r'''
import json, sys, time
from pathlib import Path
config = json.loads(Path('fixture.json').read_text())
stage = 'tasks' if 'tasks' in sys.argv else sys.argv[-1]
with Path('trace.txt').open('a') as trace:
    trace.write(stage + '\n')
time.sleep(config.get(stage + '_sleep', 0))
if stage == 'tasks':
    print(config.get('tasks_text', 'runClientGameTest - Synthetic fixture only'))
else:
    print('synthetic ' + stage)
sys.exit(config.get(stage + '_exit', 0))
'''


class VerificationPlanTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.root.chmod(0o755)
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir(mode=0o777)
        self.workspace.chmod(0o777)
        self.output = self.root / 'output'
        self.output.mkdir(mode=0o700)
        self.fake = self.workspace / 'fake_gradle.py'
        self.fake.write_text(FAKE_GRADLE, encoding='utf-8')
        (self.workspace / 'build.gradle.kts').write_text('// fabric-loom Minecraft synthetic fixture')
        self.configure()
        context = patch.object(plan, 'gradle_command', return_value=[sys.executable, str(self.fake)])
        context.start()
        self.addCleanup(context.stop)

    def configure(self, **values):
        (self.workspace / 'fixture.json').write_text(json.dumps(values), encoding='utf-8')

    def trace(self):
        file = self.workspace / 'trace.txt'
        return file.read_text().splitlines() if file.exists() else []

    def verify(self, *, eula='false', recorded=None, timeout=10):
        with patch.dict(os.environ, {'MINECRAFT_EULA_ACCEPTED': eula}):
            return plan.run_verification(timeout, workspace=self.workspace, output=self.output,
                                         recorded_run=recorded)

    def synthetic_recording(self, argv, cwd, log, timeout):
        result = plan.run_command(argv, cwd, log, timeout)
        (self.output / 'minecraft_client_capture.mp4').write_bytes(b'synthetic fixture, not a real video')
        result['video_capture'] = {'status': 'PASS', 'reason': 'Synthetic recorder plumbing fixture; not video evidence'}
        return result

    def test_build_failure_does_not_discover_launch_or_record(self):
        self.configure(build_exit=7, runClientGameTest_sleep=5)
        started = time.monotonic()
        with patch.object(plan, 'run_command', wraps=plan.run_command) as commands:
            report = self.verify(eula='true', recorded=self.synthetic_recording)
        self.assertEqual(report['status'], 'FAIL')
        self.assertEqual(report['checks'][0]['exit_code'], 7)
        self.assertEqual(self.trace(), ['build'])
        self.assertEqual(commands.call_count, 1)
        self.assertLess(time.monotonic() - started, 3)
        self.assertFalse((self.output / 'minecraft_client_capture.mp4').exists())

    def test_no_eula_runs_only_build_and_preserves_unverified_gameplay(self):
        report = self.verify()
        self.assertEqual(self.trace(), ['build'])
        self.assertEqual(report['checks'][-1]['status'], 'NOT_TESTED')
        self.assertEqual(report['status'], 'BUILD_VERIFIED_GAMEPLAY_UNVERIFIED')
        self.assertIsNone(report['video'])

    def test_one_task_discovery_and_one_client_launch_share_workspace(self):
        report = self.verify(eula='true', recorded=self.synthetic_recording)
        self.assertEqual(self.trace(), ['build', 'tasks', 'runClientGameTest'])
        self.assertEqual([c['status'] for c in report['checks']], ['PASS', 'PASS', 'PASS'])
        self.assertEqual(report['status'], 'BUILD_VERIFIED_GAMEPLAY_UNVERIFIED')
        self.assertEqual(report['gameplay_scenarios'], [])
        self.assertEqual(report['video'], 'minecraft_client_capture.mp4')
        self.assertEqual(report['video_capture']['status'], 'PASS')
        for check in report['checks']:
            self.assertIn('--daemon', check['argv'])
            self.assertIn('--max-workers=4', check['argv'])
            self.assertGreaterEqual(check['duration_seconds'], 0)
            self.assertTrue((self.output / check['log']).is_file())
        self.assertEqual(json.loads((self.output / 'verification.json').read_text()), report)

    def test_task_name_in_description_does_not_fake_a_registered_task(self):
        self.configure(tasks_text='help - Documentation mentions runClientGameTest')
        report = self.verify(eula='true', recorded=self.synthetic_recording)
        self.assertEqual(self.trace(), ['build', 'tasks'])
        self.assertEqual(report['checks'][-1]['status'], 'NOT_CONFIGURED')

    def test_discovery_failure_cannot_start_client(self):
        self.configure(tasks_exit=3)
        report = self.verify(eula='true', recorded=self.synthetic_recording)
        self.assertEqual(self.trace(), ['build', 'tasks'])
        self.assertEqual(report['status'], 'FAIL')

    def test_world_preparation_failure_keeps_build_and_never_becomes_pipeline_error(self):
        from worker import world_fixture
        with patch.object(world_fixture,'WorldFixtureSession') as factory:
            fixture=factory.return_value
            fixture.launch.side_effect=RuntimeError('Exact generator failure')
            fixture.close.return_value={'status':'ERROR','reason':'Exact generator failure'}
            report=self.verify(eula='true',recorded=self.synthetic_recording)
        self.assertEqual(self.trace(),['build','tasks'])
        self.assertEqual(report['status'],'BUILD_VERIFIED_GAMEPLAY_UNVERIFIED')
        self.assertEqual(report['checks'][-1]['status'],'NOT_TESTED')
        self.assertIn('Exact generator failure',report['checks'][-1]['detail'])
        self.assertFalse(any(c['label']=='verification_pipeline' for c in report['checks']))

    def test_template_preparation_starts_before_compilation_and_cancels_after_build_failure(self):
        from worker import world_fixture
        from unittest.mock import Mock
        fixture=Mock()
        fixture.close.return_value={'status':'NOT_EXECUTED'}
        def start(*args,**kwargs):
            self.assertEqual(self.trace(),[])
            return fixture
        self.configure(build_exit=7)
        with patch.object(world_fixture,'WorldFixtureSession',side_effect=start):
            report=self.verify(eula='true',recorded=self.synthetic_recording)
        self.assertEqual(report['checks'][0]['exit_code'],7)
        self.assertEqual(self.trace(),['build'])
        fixture.close.assert_called_once_with(cancel=True)
        fixture.launch.assert_not_called()

    def test_timeout_is_bounded_and_does_not_start_another_stage(self):
        self.configure(build_sleep=10)
        started = time.monotonic()
        real_popen = plan.subprocess.Popen
        children = []
        def spawn(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            children.append(process)
            return process
        with patch.object(plan.subprocess, 'Popen', side_effect=spawn):
            report = self.verify(eula='true', recorded=self.synthetic_recording, timeout=1)
        self.assertEqual(report['checks'][0]['status'], 'TIMEOUT')
        self.assertEqual(self.trace(), ['build'])
        self.assertLess(time.monotonic() - started, 4)
        self.assertTrue(children)
        self.assertTrue(all(process.poll() is not None for process in children))

    def test_unrecognised_project_produces_an_incomplete_report(self):
        (self.workspace / 'build.gradle.kts').unlink()
        report = self.verify()
        self.assertEqual(report['status'], 'INCOMPLETE')
        self.assertEqual(report['checks'][0]['status'], 'NOT_CONFIGURED')
        self.assertEqual(self.trace(), [])

    def test_maven_pytest_and_npm_selection(self):
        (self.workspace / 'build.gradle.kts').unlink()
        for marker, expected in [('pom.xml', 'maven_test'), ('pytest.ini', 'pytest'),
                                  ('package.json', 'npm_test')]:
            path = self.workspace / marker
            path.write_text('{"scripts":{"test":"node test.js"}}' if marker == 'package.json' else '')
            self.assertEqual(plan.select_plan(self.workspace)['label'], expected)
            path.unlink()

    def test_timeout_validation_rejects_booleans_and_invalid_values(self):
        for value in (True, 0, -1, 1.5, '60'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.verify(timeout=value)

    @unittest.skipUnless(os.name == 'posix' and hasattr(os, 'O_NOFOLLOW'), 'Descriptor artifact copy requires Linux')
    def test_artifact_copy_preserves_reports_but_never_follows_links(self):
        build = self.root / 'generated'
        (build / 'libs').mkdir(parents=True)
        (build / 'libs/result.jar').write_bytes(b'synthetic JAR fixture')
        (build / 'libs/escape.jar').symlink_to(self.fake)
        (build / 'test-results/test').mkdir(parents=True)
        (build / 'test-results/test/TEST-one.xml').write_text('<testsuite tests="1"/>')
        (build / 'cache').mkdir()
        (build / 'cache/private.txt').write_text('not an artifact')
        (build / 'run/clientGameTest/screenshots').mkdir(parents=True)
        (build / 'run/clientGameTest/screenshots/test.png').write_bytes(b'synthetic screenshot export fixture')
        (build / 'run/clientGameTest/saves/world').mkdir(parents=True)
        (build / 'run/clientGameTest/saves/world/icon.png').write_bytes(b'synthetic world icon fixture')
        jars = plan.collect_artifacts(self.output, build)
        self.assertEqual([j['filename'] for j in jars], ['result.jar'])
        self.assertEqual((self.output / 'builds/result.jar').read_bytes(), b'synthetic JAR fixture')
        self.assertFalse((self.output / 'builds/escape.jar').exists())
        self.assertTrue((self.output / 'test-results/test/TEST-one.xml').exists())
        self.assertFalse((self.output / 'cache').exists())
        self.assertTrue((self.output / 'screenshots/run-clientGameTest-screenshots-test.png').exists())
        self.assertFalse(any('icon.png' in path.name for path in (self.output / 'screenshots').iterdir()))

    @unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0,
                         'Root supervisor identity boundary requires Linux root')
    def test_project_child_drops_identity_and_cannot_write_trusted_report(self):
        program = '''import os, pathlib
assert os.getuid() == os.getgid() == 1000
assert os.getgroups() == []
try:
 pathlib.Path(''' + repr(str(self.output / 'verification.json')) + ''').write_text('forged')
except PermissionError:
 pass
else:
 raise AssertionError('Project could forge trusted report')
print(pathlib.Path('/proc/self/status').read_text())
'''
        result = plan.run_command([sys.executable, '-I', '-c', program], self.workspace,
                                  self.output / 'identity.log', 5)
        self.assertEqual(result['status'], 'PASS', result)
        self.assertRegex(result['tail'], r'CapEff:\s+0+\b')
        self.assertFalse((self.output / 'verification.json').exists())
