from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from mage_home.__main__ import check_replan, compile_design
from mage_home.blocks import MageError
from mage_home.design_history import DesignHistory
from mage_home.providers import OpenAIProvider


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.provider = OpenAIProvider({'endpoint': 'https://localhost', 'model': 'fixture', 'deadlineMillis': 0})

    def test_improving_drafts_continue_past_four_and_history_survives_restart(self):
        with TemporaryDirectory() as directory:
            history = DesignHistory(directory, 'house', 'initial', 'run-1')
            drafts = [{'candidate': n} for n in range(7)]
            outcomes = [MageError('unreachable', f'part {n}') for n in range(6)] + [{'approved': True}]
            with patch.object(self.provider, 'design', side_effect=drafts) as design, \
                    patch('mage_home.__main__.compile_plan', side_effect=outcomes):
                self.assertEqual(compile_design(self.provider, 'house', {}, 'house', history=history), {'approved': True})
            self.assertEqual(design.call_count, 7)
            restored = DesignHistory(directory, 'house', 'initial', 'run-1')
            self.assertEqual([r['plan'] for r in restored.recent], drafts[:-1])
            self.assertEqual(restored.approved['plan'], drafts[-1])
            self.assertEqual(len(list(restored.directory.glob('*.json'))), 7)

    def test_stagnation_attempts_a_simpler_strategy_then_saves_waiting_reason(self):
        with TemporaryDirectory() as directory:
            history = DesignHistory(directory, 'house', 'initial', 'run-1')
            with patch.object(self.provider, 'design', return_value={'same': True}) as design, \
                    patch('mage_home.__main__.compile_plan', side_effect=MageError('invalid', 'same constraint')):
                with self.assertRaises(MageError) as failure:
                    compile_design(self.provider, 'house', {}, 'house', history=history)
            self.assertEqual(failure.exception.code, 'stalled')
            self.assertTrue(any(c.kwargs['revision'] and c.kwargs['revision']['strategy'] == 'simplify' for c in design.call_args_list))
            self.assertEqual(json.loads(sorted(history.directory.glob('*.json'))[-1].read_bytes())['outcome'], 'waiting')

    def test_replan_preserves_distant_goals_and_obliges_removal_of_old_targets(self):
        origin = {'x': 0, 'y': 0, 'z': 0}
        floor = {**origin, 'block': 'minecraft:stone', 'state': {}}
        wall = {**floor, 'x': 15}
        original = {'origin': origin, 'revision': 1, 'cells': [floor, wall], 'requiredAir': [],
                    'requirements': {'mandatory': ['two rooms']}}
        updated = deepcopy(original)
        updated['cells'].pop(0)
        recovery = {'baseline': original, 'center': origin, 'radius': 8}
        with self.assertRaisesRegex(MageError, 'removed goals'):
            check_replan(updated, recovery)
        updated['requiredAir'] = [origin]
        check_replan(updated, recovery)
        self.assertEqual(updated['revision'], 2)
        self.assertEqual(updated['cells'], [wall])
        updated['cells'][0]['state']['facing'] = 'north'
        with self.assertRaisesRegex(MageError, 'outside the repair region'):
            check_replan(updated, recovery)
