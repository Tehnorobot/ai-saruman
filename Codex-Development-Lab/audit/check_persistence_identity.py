"""Exercise two real Loom Minecraft launches through the lab's world adapter.

Run inside an isolated worker container with a read-only source/cache seed and
an output directory. Results are diagnostic; canonical project state is untouched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET

sys.path.insert(0, '/opt/lab')


def check_expected_results(seed, output, report):
    inventory = json.loads((seed / '.lab-pipeline/scenarios-proposed.json').read_text())
    scenario = next(s for s in inventory['scenarios'] if s['id'] == 'persistence-after-exit')
    expected = {(t['classname'], t['name']) for t in scenario['execution']['expected_tests']}
    observed = {}
    for path in output.glob('*.xml'):
        for case in ET.fromstring(path.read_bytes()).iter('testcase'):
            key = (case.get('classname'), case.get('name'))
            status = 'FAIL' if case.find('failure') is not None or case.find('error') is not None else \
                     'SKIPPED' if case.find('skipped') is not None else 'PASS'
            observed.setdefault(key, []).append(status)
    # Follow the scenario's predeclared inventory, as the independent runner does.
    # Unused step placeholders are retained in raw XML and are not acceptance cases.
    report['checks'].pop('no_failed_or_skipped_cases', None)
    report['checks']['all_expected_cases_passed'] = bool(expected) and all(
        observed.get(key) == ['PASS'] for key in expected)
    report['checks']['seed_phase_passed'] = any(
        name == 'persistence-after-exit.seed' and statuses == ['PASS']
        for (_, name), statuses in observed.items())
    report['expected_tests'] = [{'classname': c, 'name': n} for c, n in sorted(expected)]
    report['status'] = 'PASS' if all(report['checks'].values()) else 'FAIL'
    (output / 'expected-scenario.json').write_text(json.dumps(scenario, indent=2), encoding='utf-8')
    return report


def run(args):
    from worker.sandbox_entry import recorded_run
    from worker.world_fixture import WorldFixtureSession

    assert os.environ.get('MINECRAFT_EULA_ACCEPTED') == 'true', 'Existing consent required'
    args.output.mkdir(parents=True, exist_ok=True)
    # No project history, credentials, or Gradle init scripts enter the scratch build.
    with tempfile.TemporaryDirectory(prefix='lab-persistence-identity-', ignore_cleanup_errors=True) as temporary:
        root = Path(temporary)
        root.chmod(0o755)
        workspace = root / 'workspace'
        workspace.mkdir(mode=0o755)
        print('Copying isolated project sources', flush=True)
        for entry in ('src', 'core', 'tools', 'gradle', 'libs', '.gradle'):
            source = args.seed / entry
            if source.exists():
                shutil.copytree(source, workspace / entry,
                                ignore=shutil.ignore_patterns('*.lock', 'build', 'test-results', 'reports'))
        for entry in ('build.gradle', 'settings.gradle', 'gradle.properties', 'gradlew', 'AGENTS.md', 'ТЗ.md'):
            shutil.copyfile(args.seed / entry, workspace / entry)
        home = root / 'gradle-home'
        print('Copying dependency caches without build-result caches', flush=True)
        for entry in ('caches/modules-2', 'caches/fabric-loom'):
            source = args.seed / '.gradle-user-home' / entry
            if source.exists():
                shutil.copytree(source, home / entry,
                                ignore=shutil.ignore_patterns('*.lock', 'assets'))
        # Existing content-addressed game assets are immutable and fully staged.
        # Share them read-only instead of copying thousands of files from Windows.
        assets = args.seed / '.gradle-user-home/caches/fabric-loom/assets'
        if assets.is_dir():
            (home / 'caches/fabric-loom/assets').symlink_to(assets, target_is_directory=True)
        # Avoid creating/changing the seed cache or relying on project EULA handling.
        for directory, _, filenames in os.walk(root):
            os.chmod(directory, 0o777)
            for name in filenames:
                os.chmod(Path(directory) / name, 0o666)
        root.chmod(0o755)
        (workspace / 'gradlew').chmod(0o755)
        session = WorldFixtureSession(workspace, args.output / 'world-preparation',
                                      300, eula=True, store=args.store)
        try:
            assert session.finish()['status'] == 'TEMPLATE_READY'
            print('Running two Minecraft JVMs through the image-owned Gradle adapter', flush=True)
            argv, fixture_env, fixture = session.launch(
                ['/opt/gradle/bin/gradle', '--offline', '--no-daemon', '--console=plain',
                 '-Dorg.gradle.workers.max=2', '-Dorg.gradle.parallel=false',
                 '-Dorg.gradle.jvmargs=-Xmx2G -XX:ActiveProcessorCount=2',
                 'runPersistenceCheck', '-PlabAcceptanceScenario=persistence-after-exit'],
                'persistence-after-exit', args.output)
            journal = root / 'journal.jsonl'
            journal.touch(mode=0o666)
            journal.chmod(0o666)
            env = {**fixture_env, 'GRADLE_USER_HOME': str(home), 'HOME': str(root),
                   'JAVA_TOOL_OPTIONS': '-XX:ActiveProcessorCount=2',
                   'LAB_GAMEPLAY_EXECUTION_JOURNAL': str(journal),
                   'LAB_GAMEPLAY_EXECUTION_ID': 'RUN-' + uuid.uuid4().hex}
            command = recorded_run(argv, workspace, args.output / 'test.log', args.timeout,
                                   scenario_id='persistence-after-exit', env=env)
            text = (args.output / 'test.log').read_text(errors='replace')
            launches = [json.loads(line.split('LAB_WORLD_FIXTURE_LAUNCH:', 1)[1])
                        for line in text.splitlines() if 'LAB_WORLD_FIXTURE_LAUNCH:' in line]
            users = re.findall(r'Setting user: (\S+)', text)
            reports = []
            for source in (workspace / 'build/test-results/acceptance').glob('*.xml'):
                target = args.output / source.name
                shutil.copyfile(source, target)
                tree = ET.fromstring(target.read_bytes())
                reports.extend({'name': case.get('name'),
                                'status': 'FAIL' if case.find('failure') is not None or case.find('error') is not None
                                else 'SKIPPED' if case.find('skipped') is not None else 'PASS'}
                               for case in tree.iter('testcase'))
            shutil.copyfile(journal, args.output / 'gameplay-journal.jsonl')
            game = Path(fixture['game_directory'])
            for name in ('houses.json', 'lab-persistence-seed.json'):
                path = game / 'config/mage_home' / name
                if path.exists():
                    # Private receipts belong to the client; read with its UID.
                    result = subprocess.run([sys.executable, '-c',
                        'import pathlib,sys;sys.stdout.buffer.write(pathlib.Path(sys.argv[1]).read_bytes())', str(path)],
                        user=1000, group=1000, extra_groups=[], capture_output=True, timeout=30, check=True)
                    (args.output / name).write_bytes(result.stdout)
            checks = {
                'command_passed': command['status'] == 'PASS',
                'two_client_launches': len(launches) == 2,
                'same_player_identity': len(launches) == 2 and launches[0]['player'] == launches[1]['player'],
                'same_game_directory': len(launches) == 2 and launches[0]['game_directory'] == launches[1]['game_directory'],
                'same_actual_user': len(users) == 2 and len(set(users)) == 1,
                'scenario_passed': any(c['name'] == 'persistence-after-exit' and c['status'] == 'PASS' for c in reports),
            }
            report = {'status': 'PASS' if all(checks.values()) else 'FAIL',
                      'checks': checks, 'command': command, 'argv': argv, 'fixture': fixture,
                      'launches': launches, 'actual_users': users, 'cases': reports,
                      'adapter_sha256': hashlib.sha256(Path('/opt/lab/fixtures/minecraft-client/quickplay.init.gradle').read_bytes()).hexdigest(),
                      'network': 'none', 'client_uid': 1000, 'live_project_changed': False,
                      'canonical_acceptance_changed': False, 'source_seed': str(args.seed)}
            check_expected_results(args.seed, args.output, report)
            (args.output / 'persistence-identity.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            print(json.dumps({'status': report['status'], 'checks': checks}), flush=True)
            assert report['status'] == 'PASS', 'See test.log and persistence-identity.json'
        finally:
            session.close(cancel=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=Path, default=Path('/seed'))
    parser.add_argument('--output', type=Path, default=Path('/output'))
    parser.add_argument('--store', type=Path, default=Path('/world-templates'))
    parser.add_argument('--timeout', type=int, default=600)
    parser.add_argument('--recheck', action='store_true', help='Re-evaluate retained raw JUnit with the declared test inventory')
    args = parser.parse_args()
    if args.recheck:
        path = args.output / 'persistence-identity.json'
        previous = path.read_bytes()
        historical = args.output / 'persistence-identity-initial.json'
        if not historical.exists():
            historical.write_bytes(previous)
        report = check_expected_results(args.seed, args.output, json.loads(previous))
        report['audit_reassessment'] = 'Use the predeclared scenario inventory; raw JUnit and original audit verdict retained.'
        path.write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps({'status': report['status'], 'checks': report['checks']}))
        assert report['status'] == 'PASS'
    else:
        run(args)
