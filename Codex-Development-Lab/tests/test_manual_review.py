import copy
import unittest

from common import manual_review


class ManualReviewTests(unittest.TestCase):
    def review(self, **changes):
        return {'category': 'physical_device',
                'reason': 'Check the tactile response of the physical controller in use.',
                'automation_blocker': 'The isolated runner has no access to the user physical controller.',
                'steps': ['Connect the controller.', 'Activate defense with the assigned button.'],
                'expected_result': 'The controller vibrates once and defense activates.', **changes}

    def plan(self, **changes):
        return {'requirements': [{'id': 'REQ-device', 'kind': 'manual', 'scenario_ids': [],
                                  'manual_review': self.review(), **changes}], 'scenarios': []}

    def test_valid_categories_and_normalized_public_fields_only(self):
        for category in manual_review.CATEGORIES:
            with self.subTest(category=category):
                value = self.review(category=category, secret='private', steps=['  Connect device.  '])
                normalized = manual_review.validate(value)
                self.assertEqual(normalized['steps'], ['Connect device.'])
                self.assertNotIn('secret', normalized)
                self.assertNotIn('secret', manual_review.decorate(
                    {'requirements': [{'id': 'REQ-device'}]}, self.plan())['requirements'][0]['manual_review'])

    def test_invalid_shapes_and_bounds(self):
        changes = [dict(category='technical_error'), dict(reason='too short'),
                   dict(reason='r' * 1001), dict(automation_blocker='short'),
                   dict(automation_blocker='b' * 1001), dict(steps=[]), dict(steps=['x'] * 13),
                   dict(steps='not a list'), dict(steps=[' ']), dict(steps=[3]),
                   dict(steps=['x' * 501]), dict(expected_result='short'),
                   dict(expected_result='x' * 1001), dict(reason='r' * 40 + '\x00')]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                manual_review.validate(self.review(**change))
        for value in (None, [], 'manual'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                manual_review.validate(value)

    def test_technical_and_provider_errors_are_not_manual_deferrals(self):
        for reason in ('Build failed and the current project cannot compile successfully.',
                       'HTTP 429 provider_error prevents the model from finishing this task.',
                       'Ошибка сборки не позволяет сейчас проверить поведение клиента.'):
            with self.subTest(reason=reason), self.assertRaises(ValueError):
                manual_review.validate(self.review(reason=reason))

    def test_default_manual_or_nonmanual_kind_is_not_implicitly_deferred(self):
        for fields in ({'manual_review': None}, {'kind': 'gameplay'}, {'scenario_ids': ['failed-check']}):
            with self.subTest(fields=fields):
                plan = self.plan(**fields)
                self.assertEqual(manual_review.pending_ids(plan), [])
                state = {'requirements': [{'id': 'REQ-device', 'status': 'NOT_TESTED'}]}
                before = copy.deepcopy(state)
                manual_review.decorate(state, plan)
                self.assertEqual(state, before)
        missing = self.plan()
        missing['requirements'][0].pop('scenario_ids')
        self.assertEqual(manual_review.pending_ids(missing), [])

    def test_explicit_scenario_mapping_cannot_hide_automatable_failure(self):
        plan = self.plan()
        plan['scenarios'] = [{'id': 'failed-check', 'requirement_ids': ['REQ-device']}]
        self.assertEqual(manual_review.pending_ids(plan), [])
        state = {'requirements': [{'id': 'REQ-device', 'status': 'FAIL'}]}
        manual_review.decorate(state, plan)
        self.assertEqual(state, {'requirements': [{'id': 'REQ-device', 'status': 'FAIL'}]})

    def test_decoration_preserves_pass_failure_counts_and_plan(self):
        plan = self.plan()
        before_plan = copy.deepcopy(plan)
        for status in ('FAIL', 'TEST_PASSED', 'NOT_TESTED'):
            state = {'status': status, 'summary': {'total': 1, 'failed': 1},
                     'requirements': [{'id': 'REQ-device', 'status': status, 'test_status': status,
                                       'scenarios': [{'id': 'old', 'status': status}]}]}
            before = copy.deepcopy(state)
            self.assertIs(manual_review.decorate(state, plan), state)
            self.assertEqual(state['requirements'][0]['review_status'], 'USER_TEST_REQUIRED')
            state['requirements'][0].pop('review_status')
            state['requirements'][0].pop('manual_review')
            self.assertEqual(state, before)
        self.assertEqual(plan, before_plan)
        self.assertEqual(manual_review.pending_ids(plan), ['REQ-device'])

    def test_duplicate_manual_ids_do_not_enable_deferral(self):
        plan = self.plan()
        plan['requirements'].append(copy.deepcopy(plan['requirements'][0]))
        self.assertEqual(manual_review.pending_ids(plan), [])


if __name__ == '__main__':
    unittest.main()
