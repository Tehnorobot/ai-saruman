"""Independent acceptance boundary tests; synthetic XML never proves Minecraft gameplay."""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from worker import acceptance_runner as runner
from common import acceptance


class AcceptanceRequestTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / 'acceptance_request.json'
        self.request = {'schema_version': 1, 'spec_sha256': 'a' * 64,
                        'catalog_ids': ['REQ-a'], 'scenarios': [{
                            'id': 'combat-one', 'kind': 'gameplay', 'task': 'runClientGameTest',
                            'requirement_ids': ['REQ-a'],
                            'expected_tests': [{'classname': 'tests.Combat', 'name': 'attack'}]}]}

    def load(self, value=None):
        self.path.write_text(json.dumps(self.request if value is None else value), encoding='utf-8')
        return runner.load_request(self.path)

    def test_missing_request_is_not_assumed_complete(self):
        self.assertIsNone(runner.load_request(self.path))

    def test_valid_exact_test_mapping_is_retained(self):
        self.assertEqual(self.load(), self.request)

    def test_unknown_requirements_invalid_tasks_and_traversal_are_rejected(self):
        for key, value in (('requirement_ids', ['REQ-missing']), ('task', '../run'),
                           ('task', '--init-script'), ('id', '../escape'),
                           ('id', 'Video'), ('kind', 'self-reported-pass')):
            with self.subTest(key=key, value=value):
                invalid = copy.deepcopy(self.request)
                invalid['scenarios'][0][key] = value
                with self.assertRaises(ValueError):
                    self.load(invalid)

    def test_duplicate_or_unbounded_scenarios_are_rejected(self):
        for count in (2, 5):
            invalid = copy.deepcopy(self.request)
            invalid['scenarios'] *= count
            with self.assertRaises(ValueError):
                self.load(invalid)

    def test_digest_and_expected_test_identity_are_required(self):
        for value in ('', 'f' * 63, 'X' * 64, None):
            invalid = copy.deepcopy(self.request)
            invalid['spec_sha256'] = value
            with self.assertRaises(ValueError):
                self.load(invalid)
        for value in ([], [{'classname': 'tests.Combat', 'name': ''}],
                      [{'classname': 'tests.Combat', 'name': 'attack\nPASS'}]):
            invalid = copy.deepcopy(self.request)
            invalid['scenarios'][0]['expected_tests'] = value
            with self.assertRaises(ValueError):
                self.load(invalid)

    def test_oversized_request_is_rejected(self):
        self.path.write_bytes(b' ' * (runner.MAX_JSON + 1))
        with self.assertRaises(ValueError):
            runner.load_request(self.path)


@unittest.skipUnless(os.name == 'posix', 'Linux descriptor-relative report security')
class AcceptanceEvidenceTests(AcceptanceRequestTests):
    def setUp(self):
        super().setUp()
        self.build = self.root / 'build'
        self.xml = self.build / 'test-results' / 'test' / 'TEST-combat.xml'
        self.xml.parent.mkdir(parents=True)
        self.expected = self.request['scenarios'][0]['expected_tests']

    def evidence(self, xml=None):
        if xml is not None:
            self.xml.write_bytes(xml.encode('utf-8') if isinstance(xml, str) else xml)
        return runner.assertion_evidence(self.build, self.root / 'evidence', self.expected)

    def test_exact_passing_case_only_establishes_test_pass_with_review_note(self):
        status, _, evidence = self.evidence('<testsuite><testcase classname="tests.Combat" name="attack"/></testsuite>')
        self.assertEqual(status, 'TEST_PASSED')
        self.assertIn('review', evidence['scope_note'])
        report = self.root / 'evidence' / evidence['reports'][0]['file']
        self.assertEqual(report.read_bytes(), self.xml.read_bytes())

    def test_missing_differently_named_case_does_not_pass(self):
        status, _, evidence = self.evidence('<testsuite><testcase classname="tests.Combat" name="other"/></testsuite>')
        self.assertEqual(status, 'NOT_TESTED')
        self.assertEqual(evidence['missing_tests'], self.expected)

    def test_failed_expected_case_does_not_pass(self):
        status, _, _ = self.evidence('<testsuite><testcase classname="tests.Combat" name="attack"><failure/></testcase></testsuite>')
        self.assertEqual(status, 'FAIL')

    def test_failed_command_retains_actual_junit_failure_for_next_round(self):
        output=self.root/'output';output.mkdir()
        message='computeOnServer can only be called from the client gametest thread'
        def command(argv,cwd,log,timeout,**kwargs):
            self.xml.write_text('<testsuite><testcase classname="tests.Combat" name="attack">'+
                '<failure message="'+message+'"/></testcase></testsuite>')
            return {'status':'FAIL','exit_code':1,'tail':'Try --help\n'*300}
        with patch.object(runner.os,'geteuid',return_value=1000):
            report=runner.run_scenarios(self.request,{'gradle':['gradle']},self.root,
                output,10,command,command,build=self.build,eula=True)
        result=report['scenarios'][0]
        self.assertEqual(result['status'],'FAIL')
        self.assertIn(message,result['detail'])
        self.assertEqual(result['assertion_evidence']['failures'][0]['message'],message)

    def test_skipped_case_does_not_pass(self):
        status, _, _ = self.evidence('<testsuite><testcase classname="tests.Combat" name="attack"><skipped/></testcase></testsuite>')
        self.assertEqual(status, 'NOT_TESTED')

    def test_dtd_and_utf16_entity_obfuscation_are_rejected(self):
        source = '<!DOCTYPE testsuite [<!ENTITY injected "PASS">]><testsuite/>'
        for index, data in enumerate((source.encode(), source.encode('utf-16'))):
            with self.subTest(index=index):
                self.xml.write_bytes(data)
                with self.assertRaises((ValueError, UnicodeError)):
                    runner.assertion_evidence(self.build, self.root / ('evidence-' + str(index)), self.expected)

    def test_symlink_xml_is_not_read_or_exported(self):
        secret = self.root / 'secret.xml'
        secret.write_text('<testsuite><testcase classname="tests.Combat" name="attack"/></testsuite>')
        self.xml.symlink_to(secret)
        status, _, evidence = self.evidence()
        self.assertEqual(status, 'NOT_TESTED')
        self.assertEqual(evidence['reports'], [])

    def test_stale_xml_is_removed_before_execution(self):
        self.xml.write_text('<testsuite><testcase classname="tests.Combat" name="attack"/></testsuite>')
        # This test owns temporary state, not a live cache; avoid identity changes in the fixture.
        with patch.object(runner.os, 'geteuid', return_value=1000):
            runner.clear_test_reports(self.build)
        self.assertFalse(self.xml.exists())

    def test_duplicate_expected_case_is_ambiguous_not_passed(self):
        status, _, _ = self.evidence('<testsuite>' +
            '<testcase classname="tests.Combat" name="attack"/>' * 2 + '</testsuite>')
        self.assertEqual(status, 'NOT_TESTED')

    def test_suite_failure_counts_do_not_allow_passing_subset(self):
        status, _, _ = self.evidence('<testsuite failures="1"><testcase classname="tests.Combat" name="attack"/></testsuite>')
        self.assertEqual(status, 'FAIL')

    def test_symlink_report_directory_is_not_followed(self):
        self.xml.parent.rmdir()
        outside = self.root / 'outside'
        outside.mkdir()
        self.xml.parent.symlink_to(outside, target_is_directory=True)
        status, _, evidence = self.evidence()
        self.assertEqual(status, 'NOT_TESTED')
        self.assertEqual(evidence['reports'], [])

    def test_successful_command_without_fresh_assertions_cannot_pass(self):
        output = self.root / 'output'
        output.mkdir()
        def command(argv, cwd, log, timeout, **kwargs):
            log.write_text('synthetic command succeeded, no assertions')
            return {'status': 'PASS', 'exit_code': 0}
        with patch.object(runner.os, 'geteuid', return_value=1000):
            report = runner.run_scenarios(self.request, {'gradle': ['gradle']}, self.root,
                output, 10, command, command, build=self.build, eula=True)
        self.assertEqual(report['scenarios'][0]['status'], 'NOT_TESTED')
        self.assertIsNone(report['scenarios'][0]['video'])

    def test_failed_command_is_not_masked_by_passing_xml(self):
        output = self.root / 'output'
        output.mkdir()
        def command(argv, cwd, log, timeout, **kwargs):
            self.xml.write_text('<testsuite><testcase classname="tests.Combat" name="attack"/></testsuite>')
            return {'status': 'FAIL', 'exit_code': 1}
        with patch.object(runner.os, 'geteuid', return_value=1000):
            report = runner.run_scenarios(self.request, {'gradle': ['gradle']}, self.root,
                output, 10, command, command, build=self.build, eula=True)
        self.assertEqual(report['scenarios'][0]['status'], 'FAIL')

    def test_gameplay_does_not_accept_eula_or_execute_when_unaccepted(self):
        output = self.root / 'output'
        output.mkdir()
        with patch.object(runner, 'clear_test_reports') as clear:
            command = unittest.mock.Mock(side_effect=AssertionError('Must not execute'))
            report = runner.run_scenarios(self.request, {'gradle': ['gradle']}, self.root,
                output, 10, command, command, build=self.build, eula=False)
        clear.assert_not_called()
        self.assertEqual(report['scenarios'][0]['status'], 'NOT_TESTED')


class AcceptanceRoutingTests(unittest.TestCase):
    """Exercise command selection; synthetic results are not gameplay evidence."""

    def test_only_gameplay_receives_client_selector(self):
        for kind in ('gameplay', 'unit', 'integration'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                scenario = {'id': 'named-check', 'kind': kind,
                            'task': 'acceptNamedCheck', 'requirement_ids': ['REQ-a'],
                            'expected_tests': [{'classname': 'tests.Check', 'name': 'run'}]}
                request = {'spec_sha256': 'a' * 64, 'scenarios': [scenario]}
                normal = unittest.mock.Mock(return_value={'status': 'PASS', 'exit_code': 0})
                recorded = unittest.mock.Mock(return_value={'status': 'PASS', 'exit_code': 0})
                with patch.object(runner, 'clear_test_reports'), patch.object(
                        runner, 'assertion_evidence', return_value=(
                            'NOT_TESTED', 'No fresh named assertions', {'reports': []})):
                    report = runner.run_scenarios(request, {'gradle': ['gradle', '--offline']},
                        root, root, 10, normal, recorded if kind == 'gameplay' else None,
                        build=root / 'build', eula=kind == 'gameplay')
                selected = recorded if kind == 'gameplay' else normal
                unused = normal if kind == 'gameplay' else recorded
                unused.assert_not_called()
                selected.assert_called_once()
                expected = ['gradle', '--offline', 'acceptNamedCheck', '--rerun-tasks']
                if kind == 'gameplay':
                    expected.append('-PlabAcceptanceScenario=named-check')
                self.assertEqual(selected.call_args.args[0], expected)
                options=selected.call_args.kwargs
                if kind == 'gameplay':
                    self.assertEqual(options['scenario_id'],'named-check')
                    self.assertEqual(set(options['env']),{'LAB_GAMEPLAY_EXECUTION_JOURNAL','LAB_GAMEPLAY_EXECUTION_ID'})
                else:
                    self.assertEqual(options,{})
                self.assertEqual(report['scenarios'][0]['status'], 'NOT_TESTED')


class AcceptanceCatalogTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.spec = '# Defense\n\n- Attack hostile targets.\n- Protect peaceful targets.\n\n## API\n\nExpose an external control API.\n'
        self.catalog = acceptance.build_catalog(self.spec)
        self.ids = [r['id'] for r in self.catalog['requirements']]
        self.raw = {'schema_version': 1, 'spec_sha256': self.catalog['spec_sha256'],
                    'requirements': [{'id': self.ids[0], 'title': 'Hostile attack',
                        'expected': 'Hostile loses health', 'kind': 'gameplay',
                        'scenario_ids': ['attack-hostile'], 'status': 'PASS'}],
                    'scenarios': [{'id': 'attack-hostile', 'kind': 'gameplay',
                        'task': 'runClientGameTest', 'requirement_ids': [self.ids[0]],
                        'expected_tests': [{'classname': 'Combat', 'name': 'attack'}]}]}

    def plan(self, raw=None):
        (self.root / 'acceptance-plan.json').write_text(json.dumps(self.raw if raw is None else raw))
        return acceptance.load_plan(self.root, self.catalog)

    def results(self, **scenario_changes):
        scenario = {'id': 'attack-hostile', 'requirement_ids': [self.ids[0]],
                    'status': 'TEST_PASSED', 'test_status': 'PASS',
                    'assertion_evidence': {'reports': [{'file': 'report.xml'}],
                        'expected_tests': [{'classname': 'Combat', 'name': 'attack'}], 'missing_tests': []},
                    'video': 'acceptance/attack-hostile/minecraft_client_capture.mp4',
                    'video_capture': {'status': 'PASS', 'scenario_window_complete': True}}
        scenario.update(scenario_changes)
        return {'spec_sha256': self.catalog['spec_sha256'], 'scenarios': [scenario]}

    def test_catalog_keeps_exact_source_lines_and_all_list_requirements(self):
        self.assertEqual(len(self.ids), 3)
        for requirement in self.catalog['requirements']:
            source = requirement['source']
            self.assertEqual(source['text'], '\n'.join(self.spec.splitlines()[source['start_line']-1:source['end_line']]))
        self.assertEqual(acceptance.build_catalog(self.spec), self.catalog)

    def test_agent_cannot_omit_unfinished_requirements_or_self_assign_pass(self):
        state = acceptance.build_state(self.catalog, self.plan(), None)
        self.assertEqual(state['summary']['total'], 3)
        self.assertEqual(state['summary']['not_tested'], 3)
        self.assertEqual(state['summary']['test_passed'], 0)
        self.assertEqual(state['status'], 'NOT_TESTED')

    def test_unknown_id_rejects_plan_atomically(self):
        invalid = copy.deepcopy(self.raw)
        invalid['requirements'].append({**invalid['requirements'][0], 'id': 'REQ-invented'})
        plan = self.plan(invalid)
        self.assertEqual(plan['requirements'], [])
        self.assertEqual(plan['scenarios'], [])
        self.assertTrue(plan['notes'])

    def test_gradle_task_alias_is_validated_and_normalized(self):
        self.raw['scenarios'][0]['gradle_task']=self.raw['scenarios'][0].pop('task')
        plan=self.plan()
        self.assertFalse(plan['notes'])
        self.assertEqual(plan['scenarios'][0]['task'],'runClientGameTest')
        self.assertNotIn('gradle_task',plan['scenarios'][0])

    def test_conflicting_or_unsafe_gradle_task_alias_rejects_entire_plan(self):
        for task in ('anotherTask','test;echo unsafe','../test'):
            with self.subTest(task=task):
                self.raw['scenarios'][0]['gradle_task']=task
                self.assertFalse(self.plan()['scenarios'])
        self.raw['scenarios'][0].pop('task')
        self.assertFalse(self.plan()['scenarios'])

    def test_plan_for_other_spec_is_not_reused(self):
        invalid = copy.deepcopy(self.raw)
        invalid['spec_sha256'] = '0' * 64
        self.assertEqual(self.plan(invalid)['scenarios'], [])

    def test_result_for_other_spec_is_not_reused(self):
        results = self.results()
        results['spec_sha256'] = '0' * 64
        state = acceptance.build_state(self.catalog, self.plan(), results)
        self.assertEqual(state['summary']['test_passed'], 0)

    def test_gameplay_requires_assertions_and_validated_video(self):
        for changes in ({'assertion_evidence': None}, {'video': None},
                        {'video_capture': {'status': 'FAIL'}}, {'status': 'PASS'},
                        {'video_capture': {'status': 'PASS', 'scenario_window_complete': False}},
                        {'video_capture': {'status': 'PASS'}}):
            with self.subTest(changes=changes):
                state = acceptance.build_state(self.catalog, self.plan(), self.results(**changes))
                self.assertEqual(state['summary']['test_passed'], 0)

    def test_one_proven_scenario_does_not_complete_unmapped_requirements(self):
        state = acceptance.build_state(self.catalog, self.plan(), self.results())
        self.assertEqual(state['summary']['test_passed'], 0)
        self.assertEqual(state['summary']['in_progress'],1)
        self.assertEqual(state['requirements'][0]['scenarios'][0]['status'],'TEST_PASSED')
        self.assertEqual(state['summary']['not_tested'], 2)
        self.assertNotIn(state['status'], ('READY', 'PASS', 'TESTS_PASSED_REVIEW_REQUIRED'))

    def test_all_synthetic_tests_passed_still_requires_review_never_ready(self):
        catalog = acceptance.build_catalog('# Defense\n\nAttack hostile targets.\n')
        original = self.raw['requirements'][0]
        plan = {'requirements': [{**original, 'id': catalog['requirements'][0]['id']}],
                'scenarios': [{**self.raw['scenarios'][0],
                               'requirement_ids': [catalog['requirements'][0]['id']]}], 'notes': []}
        results = self.results(requirement_ids=[catalog['requirements'][0]['id']])
        results['spec_sha256'] = catalog['spec_sha256']
        state = acceptance.build_state(catalog, plan, results)
        self.assertEqual(state['status'], 'TESTS_PASSED_REVIEW_REQUIRED')
        self.assertEqual(state['summary']['test_passed'], 1)

    def test_gameplay_requirement_cannot_be_satisfied_by_unit_only_without_video(self):
        invalid = copy.deepcopy(self.raw)
        invalid['scenarios'][0]['kind'] = 'unit'
        plan = self.plan(invalid)
        state = acceptance.build_state(self.catalog, plan, self.results(video=None, video_capture=None))
        self.assertEqual(state['summary']['test_passed'], 0)

    def test_selected_request_satisfies_runner_contract(self):
        request = acceptance.prepare_request(self.catalog, self.plan())
        path = self.root / 'request.json'
        path.write_text(json.dumps(request))
        self.assertEqual(runner.load_request(path), request)

    def test_more_than_four_scenarios_are_deferred_not_discarded_from_catalog(self):
        raw = copy.deepcopy(self.raw)
        raw['requirements'][0]['scenario_ids'] = ['scenario-' + str(i) for i in range(6)]
        raw['scenarios'] = [{**raw['scenarios'][0], 'id': sid}
                            for sid in raw['requirements'][0]['scenario_ids']]
        plan = self.plan(raw)
        request = acceptance.prepare_request(self.catalog, plan)
        self.assertEqual(len(request['scenarios']), 4)
        self.assertEqual(len(plan['scenarios']), 6)
        state = acceptance.build_state(self.catalog, plan, None)
        self.assertEqual(len(state['requirements'][0]['scenarios']), 6)

    def test_failed_initial_batch_does_not_starve_never_attempted_scenarios(self):
        raw = copy.deepcopy(self.raw)
        ids = ['scenario-' + str(i) for i in range(6)]
        raw['requirements'][0]['scenario_ids'] = ids
        raw['scenarios'] = [{**raw['scenarios'][0], 'id': sid} for sid in ids]
        plan = self.plan(raw)
        previous = {'spec_sha256': self.catalog['spec_sha256'], 'source_sha256': 'old-source',
                    'round': 1, 'requirements': [{'scenarios': [
                        {'id': sid, 'status': 'FAIL', 'test_status': 'FAIL', 'attempt_round': 1}
                        for sid in ids[:4]]}]}
        request = acceptance.prepare_request(self.catalog, plan, previous, source_sha256='new-source')
        selected = [s['id'] for s in request['scenarios']]
        self.assertEqual(selected[:2], ids[4:])

    def test_source_change_invalidates_fingerprint_but_build_outputs_do_not(self):
        source = self.root / 'Main.java'
        source.write_text('first implementation')
        before = acceptance.source_fingerprint(self.root)
        (self.root / 'build').mkdir()
        (self.root / 'build' / 'random.bin').write_bytes(b'untrusted build cache')
        self.assertEqual(acceptance.source_fingerprint(self.root), before)
        source.write_text('changed implementation')
        self.assertNotEqual(acceptance.source_fingerprint(self.root), before)

    def test_path_traversal_and_absolute_json_paths_are_rejected(self):
        for path in ('../outside.json', '/etc/passwd'):
            with self.assertRaises(ValueError):
                acceptance.read_json(self.root, path)


class AcceptanceHttpTests(unittest.TestCase):
    """HTTP fixtures exercise evidence links, not real test/video authenticity."""
    def setUp(self):
        from tests.test_extend_rounds import ExtendRoundsTests
        ExtendRoundsTests.setUp(self)
        self.catalog = acceptance.build_catalog(acceptance.read_spec(self.projects / self.pid))
        rid = self.catalog['requirements'][0]['id']
        self.plan_raw = {'schema_version': 1, 'spec_sha256': self.catalog['spec_sha256'],
            'requirements': [{'id': rid, 'title': 'Synthetic requirement', 'expected': 'Fixture assertion',
                              'kind': 'gameplay', 'scenario_ids': ['one-game']}],
            'scenarios': [{'id': 'one-game', 'kind': 'gameplay', 'task': 'runClientGameTest',
                           'requirement_ids': [rid],
                           'expected_tests': [{'classname': 'Fixture', 'name': 'assertion'}]}]}
        self.base = '/api/jobs/' + self.jid + '/acceptance'

    def save_proof(self):
        (self.workspace / 'acceptance-plan.json').write_text(json.dumps(self.plan_raw))
        plan = acceptance.load_plan(self.workspace, self.catalog)
        relative = 'acceptance/one-game/'
        folder = self.stage5 / relative
        (folder / 'assertions').mkdir(parents=True)
        xml = b'<testsuite><testcase classname="Fixture" name="assertion"/></testsuite>'
        (folder / 'assertions' / 'report-000.xml').write_bytes(xml)
        (folder / 'test.log').write_text('Synthetic HTTP evidence fixture')
        (folder / 'minecraft_client_capture.mp4').write_bytes(b'synthetic link fixture, not Minecraft evidence')
        result = {'id': 'one-game', 'requirement_ids': self.plan_raw['scenarios'][0]['requirement_ids'],
            'status': 'TEST_PASSED', 'test_status': 'PASS', 'artifact_round': 'round-05',
            'video': relative + 'minecraft_client_capture.mp4', 'log': relative + 'test.log',
            'video_capture': {'status': 'PASS', 'scenario_window_complete': True},
            'assertion_evidence': {'reports': [{'file': relative + 'assertions/report-000.xml',
                                               'sha256': hashlib.sha256(xml).hexdigest()}],
                'expected_tests': self.plan_raw['scenarios'][0]['expected_tests'], 'missing_tests': []}}
        state = acceptance.build_state(self.catalog, plan,
            {'spec_sha256': self.catalog['spec_sha256'], 'scenarios': [result]},
            source_sha256=acceptance.source_fingerprint(self.workspace), round_number=5)
        self.saved = self.output / 'latest_acceptance.json'
        self.saved.write_text(json.dumps(state))
        return folder

    def get(self):
        response = self.client.get(self.base)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_unknown_job_is_404(self):
        self.assertEqual(self.client.get('/api/jobs/missing/acceptance').status_code, 404)

    def test_existing_job_get_is_read_only_and_never_assigns_generic_video_to_requirements(self):
        (self.stage5 / 'minecraft_client_capture.mp4').write_bytes(b'generic fixture')
        before = {p.relative_to(self.root): p.read_bytes() for owner in (self.projects, self.artifacts)
                  for p in owner.rglob('*') if p.is_file()}
        state = self.get()
        after = {p.relative_to(self.root): p.read_bytes() for owner in (self.projects, self.artifacts)
                 for p in owner.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(state['summary']['test_passed'], 0)
        self.assertTrue(state['requirements'])
        self.assertTrue(all(not r['scenarios'] for r in state['requirements']))

    def test_current_proof_links_are_local_and_protected_read_only(self):
        self.save_proof()
        before = self.saved.read_bytes()
        state = self.get()
        self.assertEqual(state['summary']['test_passed'], 0)
        self.assertEqual(state['requirements'][0]['status'],'IN_PROGRESS')
        scenario = state['requirements'][0]['scenarios'][0]
        self.assertEqual(scenario['status'],'TEST_PASSED')
        self.assertEqual(scenario['video_url'], '/api/jobs/' + self.jid +
                         '/files/round-05/acceptance/one-game/minecraft_client_capture.mp4')
        self.assertEqual(self.saved.read_bytes(), before)

    def test_grouped_tasks_preserve_source_inventory_and_validate_proof_first(self):
        folder=self.save_proof()
        state=self.get()
        self.assertCountEqual([rid for task in state['tasks'] for rid in task['requirement_ids']],
                              [r['id'] for r in state['requirements']])
        self.assertEqual(state['task_summary']['total'],len(state['tasks']))
        self.assertEqual(state['task_summary']['test_passed'],0)
        self.assertEqual(state['task_summary']['in_progress'],1)
        self.assertEqual(state['requirements'][0]['scenarios'][0]['status'],'TEST_PASSED')
        (folder/'assertions'/'report-000.xml').write_bytes(b'changed proof')
        self.assertEqual(self.get()['task_summary']['test_passed'],0)
        self.assertEqual(self.get()['requirements'][0]['scenarios'][0]['status'],'NOT_TESTED')

    def test_changed_source_or_running_round_is_stale(self):
        from common import db
        self.save_proof()
        self.sentinel.write_bytes(b'changed implementation')
        state = self.get()
        self.assertEqual(state['summary']['test_passed'], 0)
        self.assertEqual(state['requirements'][0]['status'], 'STALE')
        db.update(self.jid, status='RUNNING')
        self.assertEqual(self.get()['summary']['test_passed'], 0)

    def test_changed_assertion_report_revokes_pass(self):
        folder = self.save_proof()
        (folder / 'assertions' / 'report-000.xml').write_bytes(b'changed after verification')
        self.assertEqual(self.get()['summary']['test_passed'], 0)

    def test_verification_on_unchanged_source_preserves_current_proof(self):
        from common import db
        self.save_proof()
        db.update(self.jid,status='RUNNING',phase='verifying')
        state=self.get()
        self.assertTrue(state['source_current'])
        self.assertEqual(state['summary']['test_passed'],0)
        self.assertEqual(state['requirements'][0]['scenarios'][0]['status'],'TEST_PASSED')

    def test_changed_source_preserves_history_but_resets_current_queue(self):
        from common import acceptance_progress
        self.save_proof()
        plan=acceptance.load_plan(self.workspace,self.catalog)
        saved=json.loads(self.saved.read_text())
        ledger=acceptance_progress.load_progress(self.output,self.catalog,plan)
        ledger=acceptance_progress.record_results(ledger,self.catalog,plan,saved,5,saved['source_sha256'])
        acceptance_progress.save_progress(self.output,ledger)
        self.sentinel.write_bytes(b'new implementation')
        state=self.get()
        self.assertFalse(state['source_current'])
        self.assertEqual(state['summary']['test_passed'],0)
        self.assertEqual(state['summary']['stale'],1)
        self.assertEqual(state['history_summary']['test_passed'],0)
        self.assertEqual(state['history_summary']['scenarios_ever_passed'],1)
        self.assertEqual(state['queue_summary']['pending_current'],1)
        self.assertEqual(state['queue_summary']['current_tested'],0)

    def test_external_urls_and_path_traversal_are_never_exposed_as_video_links(self):
        self.save_proof()
        original = json.loads(self.saved.read_text())
        for path in ('https://example.invalid/track.mp4', '../other.mp4', '/etc/passwd',
                     'acceptance/one-game/../../other.mp4'):
            with self.subTest(path=path):
                state = copy.deepcopy(original)
                state['requirements'][0]['scenarios'][0]['video'] = path
                self.saved.write_text(json.dumps(state))
                result = self.get()
                self.assertIsNone(result['requirements'][0]['scenarios'][0]['video_url'])
                self.assertEqual(result['summary']['test_passed'], 0)

    @unittest.skipUnless(os.name == 'posix', 'Linux nofollow evidence boundary')
    def test_symlink_video_is_not_linked_or_accepted(self):
        folder = self.save_proof()
        video = folder / 'minecraft_client_capture.mp4'
        video.unlink()
        outside = self.root / 'outside.mp4'
        outside.write_bytes(b'private unrelated file')
        video.symlink_to(outside)
        state = self.get()
        self.assertIsNone(state['requirements'][0]['scenarios'][0]['video_url'])
        self.assertEqual(state['summary']['test_passed'], 0)


if __name__ == '__main__':
    unittest.main()
