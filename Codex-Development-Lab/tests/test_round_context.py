"""Cross-round claims stay separate from independent verification evidence."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from common import acceptance
from worker.round_context import prepare_context, failure_excerpt


class RoundContextTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.ws = self.root / 'project' / 'workspace'
        self.ws.mkdir(parents=True)
        self.output = self.root / 'artifacts'
        self.output.mkdir()
        self.spec = self.ws.parent / 'ТЗ.md'
        self.spec.write_text('# Root\nintro\n## Combat\nattack\n## UI\nshow\n', encoding='utf-8')

    def report(self, number, status='FAIL', label='build', tail='Compile failed'):
        value = {'status': status, 'checks': [{'label': label, 'status': status, 'tail': tail}]}
        folder = self.output / f'round-{number:02d}'
        folder.mkdir(exist_ok=True)
        (folder / 'verification_complete.json').write_text(json.dumps(value), encoding='utf-8')
        return value

    def prepare(self, n, previous=None):
        return prepare_context(self.ws, self.output, n, previous)

    def memory(self):
        return json.loads((self.output / 'round-memory.json').read_text(encoding='utf-8'))

    def test_trusted_nested_video_capture_success_resolves_old_capture_error(self):
        self.report(1,label='video_capture',tail='blank recording')
        self.prepare(2)
        self.prepare(3,{'checks':[],'acceptance_results':{'scenarios':[
            {'video_capture':{'status':'PASS'}}]}})
        error=self.memory()['errors'][0]
        self.assertEqual(error['status'],'RESOLVED')
        self.assertEqual(error['resolved_round'],2)

    def test_invalid_plan_repair_precedes_other_failures_and_is_visible(self):
        (self.ws/'acceptance-plan.json').write_text('{"schema_version":1}')
        previous=self.report(1,tail='other failure')
        context=self.prepare(2,previous)
        self.assertEqual(context['round_action']['kind'],'repair_acceptance_plan')
        self.assertTrue(context['acceptance_plan_notes'])
        self.assertEqual(self.memory()['round_action'],context['round_action'])

    def test_failure_excerpt_keeps_selector_error_instead_of_gradle_help(self):
        root='> Set -PlabAcceptanceScenario to one client scenario id: [combat]'
        tail='> Task :compileJava UP-TO-DATE\n'*100+'What went wrong:\nExecution failed for task\n'+root+'\n'+'Try --help\n'*150
        previous=self.report(1,label='client_gametest',tail=tail)
        context=self.prepare(2,previous)
        self.assertIn(root,failure_excerpt(tail))
        self.assertIn(root,context['previous_independent_verification']['checks'][0]['tail'])
        self.assertIn(root,self.memory()['errors'][0]['detail'])

    def test_scenario_memory_keeps_junit_error_when_command_tail_is_only_help(self):
        root='AssertionError: UnionClef held an input while defense owned controls'
        report={'checks':[],'acceptance_results':{'scenarios':[
            {'id':'handover','status':'FAIL','detail':root,'command_tail':'Try --help\n'*300}]}}
        self.prepare(2,report)
        errors=[e for e in self.memory()['errors'] if e['label']=='scenario:handover']
        self.assertTrue(errors)
        self.assertIn(root,errors[0]['detail'])

    def test_notes_do_not_invalidate_source_proof_but_code_and_plan_do(self):
        source=self.ws/'Combat.java'
        source.write_text('class Combat {}')
        before=acceptance.source_fingerprint(self.ws)
        self.prepare(1)
        (self.ws/'agent-handoff.json').write_text('{"summary":"next step"}')
        self.assertEqual(acceptance.source_fingerprint(self.ws),before)
        source.write_text('class Combat { int changed; }')
        changed=acceptance.source_fingerprint(self.ws)
        self.assertNotEqual(changed,before)
        (self.ws/'acceptance-plan.json').write_text('{"scenarios":[]}')
        self.assertNotEqual(acceptance.source_fingerprint(self.ws),changed)

    def test_grouped_task_document_is_generated_context_not_source_or_proof(self):
        before=acceptance.source_fingerprint(self.ws)
        context=self.prepare(1)
        document=json.loads((self.ws/'acceptance-tasks.json').read_text(encoding='utf-8'))
        catalog=acceptance.build_catalog(acceptance.read_spec(self.ws.parent))
        self.assertEqual(document['spec_sha256'],catalog['spec_sha256'])
        self.assertCountEqual([rid for task in document['tasks'] for rid in task['requirement_ids']],
                              [r['id'] for r in catalog['requirements']])
        self.assertEqual(context['documents']['tasks'],'acceptance-tasks.json')
        self.assertEqual(len(context['task_index']),len(document['tasks']))
        self.assertTrue(all('requirement_ids' not in task for task in context['task_index']))
        self.assertEqual(acceptance.source_fingerprint(self.ws),before)
        self.assertEqual(document['summary']['test_passed'],0)

    def test_task_context_assigns_groups_and_exact_upcoming_scenarios(self):
        catalog=acceptance.build_catalog(acceptance.read_spec(self.ws.parent))
        rid=catalog['requirements'][1]['id']
        plan={'schema_version':1,'spec_sha256':catalog['spec_sha256'],
            'requirements':[{'id':rid,'title':'Combat','expected':'assertion','kind':'unit','scenario_ids':['combat-check']}],
            'scenarios':[{'id':'combat-check','kind':'unit','task':'testCombat',
                'requirement_ids':[rid],'expected_tests':[{'classname':'CombatTest','name':'combat'}]}]}
        (self.ws/'acceptance-plan.json').write_text(json.dumps(plan))
        context=self.prepare(1)
        self.assertEqual(context['upcoming_scenarios'],[{'id':'combat-check','requirement_ids':[rid]}])
        self.assertEqual(len(context['focus_task_ids']),1)
        document=json.loads((self.ws/'acceptance-tasks.json').read_text(encoding='utf-8'))
        task=next(t for t in document['tasks'] if t['id']==context['focus_task_ids'][0])
        self.assertIn(rid,task['requirement_ids'])

    def test_group_index_is_bounded_and_reports_omitted_groups(self):
        self.spec.write_text('# Mod\n\n'+'\n\n'.join(
            '## '+str(n)+'x'*1000+'\n\n- Rule '+str(n) for n in range(50)),encoding='utf-8')
        context=self.prepare(1)
        self.assertEqual(context['task_index_total'],50)
        self.assertEqual(len(context['task_index']),32)
        self.assertEqual(context['task_index_omitted'],18)
        self.assertTrue(all(len(task['title'])<=140 for task in context['task_index']))
        self.assertEqual(len(json.loads((self.ws/'acceptance-tasks.json').read_text(encoding='utf-8'))['tasks']),50)

    def test_index_points_to_exact_sections_without_including_contents(self):
        context = self.prepare(1)
        index = json.loads((self.ws / 'document-index.json').read_text(encoding='utf-8'))
        self.assertEqual(index['sections'][1], {
            'title': 'Combat', 'level': 2, 'start_line': 3, 'end_line': 4})
        self.assertEqual(index['sections'][0]['end_line'], 6)
        self.assertNotIn('intro', json.dumps(context))
        self.assertEqual(context['documents']['normative_spec'], 'ТЗ.md')

    def test_repeated_error_is_counted_once_per_round_not_preparation(self):
        previous = self.report(1)
        self.prepare(2, previous)
        self.prepare(2, previous)
        self.assertEqual(self.memory()['errors'][0]['repeat_count'], 1)
        self.report(2)
        self.prepare(3, previous)
        error = self.memory()['errors'][0]
        self.assertEqual((error['repeat_count'], error['first_round'], error['last_round']), (2, 1, 2))

    def test_only_later_matching_independent_pass_resolves_error(self):
        self.report(1)
        self.report(2, status='PASS', label='unrelated')
        self.prepare(3)
        self.assertEqual(self.memory()['errors'][0]['status'], 'OPEN')
        self.report(3, status='PASS')
        self.prepare(4)
        error = self.memory()['errors'][0]
        self.assertEqual(error['status'], 'RESOLVED')
        self.assertEqual(error['resolved_round'], 3)
        self.assertIn('Independent round 3', error['resolution'])
        self.report(4)
        self.prepare(5)
        self.assertEqual(self.memory()['errors'][0]['status'], 'OPEN')
        self.assertNotIn('resolution', self.memory()['errors'][0])

    def test_fake_workspace_memory_and_agent_pass_cannot_resolve_error(self):
        self.report(1)
        self.prepare(2)
        (self.ws / 'round-memory.json').write_text('{"errors": []}', encoding='utf-8')
        (self.ws / 'agent-handoff.json').write_text(json.dumps({
            'summary': 'All checks PASS', 'status': 'PASS', 'api_key': 'secret',
            'errors_and_solutions': [{'error': 'compile', 'solution': 'fixed',
                                      'evidence': 'claim', 'authorization': 'secret'}]}), encoding='utf-8')
        context = self.prepare(2)
        self.assertEqual(context['open_error_count'], 1)
        self.assertIn('UNVERIFIED', context['agent_handoff']['provenance'])
        self.assertNotIn('secret', json.dumps(context))
        self.assertEqual(len(json.loads((self.ws / 'round-memory.json').read_text())['errors']), 1)

    def test_stale_handoff_dropped_and_spec_digest_tracks_changed_bytes(self):
        self.report(1)
        self.prepare(2)
        (self.ws / 'agent-handoff.json').write_text(json.dumps({
            'spec_sha256': 'b' * 64, 'summary': 'old implementation'}), encoding='utf-8')
        self.assertTrue(self.prepare(2)['agent_handoff']['stale'])
        self.spec.write_text('# New specification\n', encoding='utf-8')
        # Older reports have no matching spec digest: explicitly ensure the new
        # digest is current; historical failures remain historical diagnostics.
        context = self.prepare(2)
        self.assertEqual(context['spec_sha256'], hashlib.sha256(self.spec.read_bytes()).hexdigest())

    def test_legacy_backfill_and_prompt_are_bounded(self):
        for n in range(1, 36):
            self.report(n, label=f'check{n}', tail='x' * 5000)
        context = self.prepare(36)
        self.assertEqual(len(self.memory()['errors']), 30)
        self.assertEqual(len(context['recent_open_errors']), 5)
        self.assertTrue(all(len(item['detail']) <= 400 for item in context['recent_open_errors']))
        self.assertLess(len(json.dumps(context)), 8000)
        self.assertEqual(self.memory()['errors'][0]['first_round'], 6)

    def test_no_future_or_symlinked_report_reads(self):
        self.report(4)
        self.prepare(2)
        self.assertEqual(self.memory()['errors'], [])
        try:
            (self.ws / 'agent-handoff.json').symlink_to(self.spec)
        except OSError:
            self.skipTest('Symlinks unavailable on this Windows host')
        self.assertFalse(self.prepare(2)['agent_handoff']['available'])

    def test_fenced_headings_ignored_and_index_bounded(self):
        self.spec.write_text('```markdown\n# not heading\n```\n' +
                             '\n'.join(f'## section {i}' for i in range(130)), encoding='utf-8')
        self.prepare(1)
        index = json.loads((self.ws / 'document-index.json').read_text())
        self.assertEqual(len(index['sections']), 120)
        self.assertEqual(index['truncated_count'], 10)
        self.assertEqual(index['sections'][0]['start_line'], 4)

    def test_immediate_previous_report_persists_before_pause(self):
        self.prepare(2, {'status': 'FAIL', 'checks': [
            {'label': 'verification_session', 'status': 'FAIL', 'tail': 'permission denied'}]})
        self.assertEqual(self.memory()['errors'][0]['last_round'], 1)

    def test_migration_uses_only_last_public_main_message(self):
        self.report(1)
        events = [
            {'type': 'item.completed', 'item': {'type': 'agent_message', 'agent_id': 'main', 'text': 'old'}},
            {'type': 'item.completed', 'item': {'type': 'agent_message', 'agent_id': 'main', 'text': 'current decision'}},
            {'type': 'item.completed', 'item': {'type': 'agent_message', 'agent_id': 'reviewer', 'text': 'child claim'}},
            {'type': 'item.completed', 'item': {'type': 'command_execution', 'aggregated_output': 'large output'}}]
        (self.output / 'round-01' / 'codex.jsonl').write_text(
            '\n'.join(json.dumps(event) for event in events), encoding='utf-8')
        handoff = self.prepare(2)['agent_handoff']
        self.assertEqual(handoff['summary'], 'current decision')
        self.assertIn('UNVERIFIED', handoff['provenance'])

    def test_migration_skips_oversized_public_log(self):
        self.report(1)
        (self.output / 'round-01' / 'codex.jsonl').write_bytes(b'x' * (2 * 1024 * 1024 + 1))
        self.assertFalse(self.prepare(2)['agent_handoff']['available'])

    def test_same_round_reverification_updates_without_double_counting(self):
        self.report(1)
        self.prepare(2)
        changed = self.report(1)
        changed['duration_seconds'] = 123
        (self.output / 'round-01' / 'verification_complete.json').write_text(json.dumps(changed))
        self.prepare(2)
        self.assertEqual(self.memory()['errors'][0]['repeat_count'], 1)
        self.report(1, status='PASS')
        self.prepare(2)
        self.assertEqual(self.memory()['errors'][0]['status'], 'RESOLVED')

    def test_actual_build_execution_resolves_session_startup_not_mod_failure(self):
        self.report(1, label='verification_session', tail='Permission denied')
        self.prepare(2)
        report = {'status': 'FAIL', 'checks': [{'label': 'gradle_build',
                    'status': 'FAIL', 'exit_code': 1, 'tail': 'Compile error'}]}
        self.prepare(3, report)
        errors = {error['label']: error for error in self.memory()['errors']}
        self.assertEqual(errors['verification_session']['status'], 'RESOLVED')
        self.assertEqual(errors['gradle_build']['status'], 'OPEN')
        self.assertIn('startup recovered', errors['verification_session']['resolution'])

    def test_detail_fallback_keeps_distinct_infrastructure_failures(self):
        for number, detail in ((1, 'bad owner'), (2, 'missing image')):
            self.prepare(number + 1, {'checks': [
                {'label': 'verification_session', 'status': 'ERROR', 'detail': detail}]})
        self.assertEqual(len(self.memory()['errors']), 2)
        self.assertEqual({error['detail'] for error in self.memory()['errors']}, {'bad owner', 'missing image'})

    def test_same_round_valid_report_resolves_report_parse_failure(self):
        self.report(1, status='ERROR', label='verification_report', tail='Malformed report')
        self.prepare(2)
        self.prepare(2, {'status': 'FAIL', 'checks': [{'label': 'gradle_build',
            'status': 'FAIL', 'exit_code': 1, 'tail': 'real compile failure'}]})
        errors = {error['label']: error for error in self.memory()['errors']}
        self.assertEqual(errors['verification_report']['status'], 'RESOLVED')
        self.assertEqual(errors['gradle_build']['status'], 'OPEN')

    def test_old_agent_solutions_retained_as_unverified_after_handoff_changes(self):
        path = self.ws / 'agent-handoff.json'
        path.write_text(json.dumps({'errors_and_solutions': [
            {'error': 'wall', 'solution': 'approach fallback', 'evidence': 'local claim'}]}))
        self.prepare(2)
        path.write_text(json.dumps({'summary': 'working on next issue'}))
        self.prepare(3)
        notes = self.memory()['recent_agent_notes']
        self.assertEqual(len(notes), 2)
        self.assertIsNone(notes[0]['source_round'])  # no trusted snapshot proves its origin
        self.assertIn('UNVERIFIED', notes[0]['provenance'])
        self.assertEqual(notes[0]['claims']['errors_and_solutions'][0]['solution'], 'approach fallback')

    def test_scenario_error_resolves_only_when_that_scenario_passes(self):
        def report(sid,status,remaining=1):return {'checks':[],
            'scenario_failures_remaining':remaining,'acceptance_results':{'scenarios':[
                {'id':sid,'status':status,'detail':'Actual assertion result'}]}}
        self.prepare(2,report('combat','FAIL'))
        self.prepare(3,report('food','TEST_PASSED'))
        self.assertEqual(self.memory()['errors'][0]['status'],'OPEN')
        self.prepare(4,report('combat','TEST_PASSED',0))
        self.assertEqual(self.memory()['errors'][0]['status'],'RESOLVED')

    def test_assignment_context_does_not_embed_full_failure_history(self):
        from worker.round_context import compact_assignment
        value={'current_task_id':'task-one','failure_memory':{'secret':'x'*100000},
            'completed_bindings':{'one':'a'*64},'regression_requirement_ids':['req']*1000,
            'current_task':{'id':'task-one','title':'x'*1000,
                'requirement_ids':['req']*1000,'unresolved_requirement_ids':['req']*1000}}
        compact=compact_assignment(value)
        self.assertNotIn('failure_memory',compact)
        self.assertNotIn('completed_bindings',compact)
        self.assertLess(len(json.dumps(compact)),4000)

    def test_later_executed_scenario_archives_aggregate_failure_without_claiming_resolution(self):
        previous = self.report(1, label='minecraft_client_gametest', tail='Old aggregate launch failed')
        self.prepare(2, previous)
        context = self.prepare(3, {'checks': [], 'acceptance_results': {'scenarios': [
            {'id': 'combat', 'status': 'TEST_PASSED'}]}})
        aggregate = next(e for e in self.memory()['errors'] if e['label'] == 'minecraft_client_gametest')
        self.assertEqual(aggregate['status'], 'HISTORICAL')
        self.assertEqual(aggregate['detail'], 'Old aggregate launch failed')
        self.assertNotIn('resolved_round', aggregate)
        self.assertIn('not comparable', aggregate['resolution'].lower())
        self.assertEqual(context['open_error_count'], 0)
        # Historical diagnostics must survive subsequent context validation.
        self.prepare(4)
        self.assertEqual(self.memory()['errors'][0]['status'], 'HISTORICAL')

    def test_later_failed_scenario_archives_aggregate_but_keeps_exact_failure_open(self):
        self.report(1, label='minecraft_client_gametest', tail='Old aggregate launch failed')
        self.prepare(2)
        self.prepare(3, {'checks': [], 'acceptance_results': {'scenarios': [
            {'id': 'combat', 'status': 'FAIL', 'detail': 'Real combat assertion failed'}]}})
        errors = {e['label']: e for e in self.memory()['errors']}
        self.assertEqual(errors['minecraft_client_gametest']['status'], 'HISTORICAL')
        self.assertEqual(errors['scenario:combat']['status'], 'OPEN')
        self.assertIn('Real combat assertion failed', errors['scenario:combat']['detail'])

    def test_same_round_scenario_execution_does_not_archive_aggregate_failure(self):
        self.prepare(2, {'checks': [{'label': 'minecraft_client_gametest', 'status': 'FAIL',
            'tail': 'Aggregate and exact scenario failed together'}], 'acceptance_results': {'scenarios': [
                {'id': 'combat', 'status': 'FAIL', 'detail': 'Combat assertion'}]}})
        errors = {e['label']: e for e in self.memory()['errors']}
        self.assertEqual(errors['minecraft_client_gametest']['status'], 'OPEN')
        self.assertEqual(errors['scenario:combat']['status'], 'OPEN')
        self.prepare(3)
        self.assertEqual(next(e for e in self.memory()['errors']
                              if e['label'] == 'minecraft_client_gametest')['status'], 'OPEN')

    def test_missing_or_unexecuted_scenarios_do_not_archive_aggregate_failure(self):
        self.report(1, label='minecraft_client_gametest', tail='Old aggregate launch failed')
        self.prepare(2)
        for number, report in enumerate((
            {'checks': [{'label': 'gradle_build', 'status': 'PASS', 'exit_code': 0}]},
            {'checks': [], 'acceptance_results': {'scenarios': []}},
            {'checks': [], 'acceptance_results': {'scenarios': [
                {'id': 'combat', 'status': 'NOT_TESTED'}]}},
            {'checks': [], 'acceptance_results': {'scenarios': [
                {'id': 'combat', 'status': 'STALE'}]}},
        ), 3):
            with self.subTest(round=number):
                self.prepare(number, report)
                self.assertEqual(self.memory()['errors'][0]['status'], 'OPEN')

    def test_matching_pass_resolves_historical_aggregate_and_exact_failure_reopens_it(self):
        self.report(1, label='minecraft_client_gametest', tail='Old aggregate launch failed')
        self.prepare(2)
        self.prepare(3, {'checks': [], 'acceptance_results': {'scenarios': [
            {'id': 'combat', 'status': 'TEST_PASSED'}]}})
        self.assertEqual(self.memory()['errors'][0]['status'], 'HISTORICAL')
        self.prepare(4, {'checks': [{'label': 'minecraft_client_gametest', 'status': 'PASS'}]})
        error = self.memory()['errors'][0]
        self.assertEqual(error['status'], 'RESOLVED')
        self.assertEqual(error['resolved_round'], 3)
        self.prepare(5, {'checks': [{'label': 'minecraft_client_gametest', 'status': 'FAIL',
                                   'tail': 'Old aggregate launch failed'}]})
        error = self.memory()['errors'][0]
        self.assertEqual(error['status'], 'OPEN')
        self.assertEqual(error['last_round'], 4)
        self.assertNotIn('resolved_round', error)
        self.assertNotIn('resolution', error)

    def test_unrelated_executed_scenario_does_not_archive_exact_scenario_failure(self):
        self.prepare(2, {'checks': [], 'acceptance_results': {'scenarios': [
            {'id': 'combat', 'status': 'FAIL', 'detail': 'Combat assertion failed'}]}})
        self.prepare(3, {'checks': [], 'acceptance_results': {'scenarios': [
            {'id': 'food', 'status': 'TEST_PASSED'}]}})
        error = self.memory()['errors'][0]
        self.assertEqual(error['label'], 'scenario:combat')
        self.assertEqual(error['status'], 'OPEN')


if __name__ == '__main__':
    unittest.main()
