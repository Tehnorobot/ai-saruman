"""Exercise real Gradio save/timer callbacks without an agent, Redis or Minecraft."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import gradio as gr

from saruman.autonomy import Autonomy
from saruman.autonomy_panel import AutonomyPanel, add_autonomy_panel
from saruman.autonomy_settings import catalog, load_weights, probabilities, save_weights


class SettingsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "settings.json"

    def test_relative_weights_normalize_and_disabled_entries_have_no_chance(self):
        rows = probabilities(
            catalog(),
            {"farm": 50, "hunt": 25, "explore": 25, "trees": 0},
            {"farm": 60},
            10,
        )
        self.assertEqual([a["probability"] for a in rows], [50, 25, 25, 0])
        self.assertEqual([a["next_probability"] for a in rows], [0, 50, 50, 0])
        self.assertEqual(rows[0]["cooldown_seconds"], 50)

    def test_invalid_values_leave_previous_file_unchanged(self):
        weights = {"farm": 3, "hunt": 2, "explore": 2, "trees": 0}
        save_weights(weights, self.path)
        previous = self.path.read_bytes()
        for bad in (-1, float("nan"), float("inf"), True, "10", None, 1_000_001):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                save_weights({**weights, "hunt": bad}, self.path)
            self.assertEqual(self.path.read_bytes(), previous)
        with self.assertRaises(ValueError):
            save_weights({"unknown": 1}, self.path)

    def test_saved_weights_survive_new_controller_and_are_not_written_to_catalog(self):
        before = catalog()
        weights = {"farm": 0, "hunt": 1, "explore": 0, "trees": 0}
        save_weights(weights, self.path)
        self.assertEqual(load_weights(self.path), weights)
        self.assertEqual(Autonomy(settings_path=self.path).weights, weights)
        self.assertEqual(catalog(), before)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])


class PanelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "settings.json"
        save_weights({"farm": 3, "hunt": 2, "explore": 2, "trees": 0}, self.path)
        self.game = {"ingame": True}
        with gr.Blocks() as self.app:
            self.panel = add_autonomy_panel(lambda: (self.game, True), self.path)
        self.functions = {
            f.fn.__name__: (i, f) for i, f in self.app.fns.items() if f.fn is not None
        }

    async def test_save_uses_plain_callback_without_event_payload(self):
        index, _ = self.functions["apply"]
        result = await self.app.process_api(index, [0, 100, 0, 0])
        self.assertIn("100.0%", result["data"][0])
        self.assertEqual(
            load_weights(self.path), {"farm": 0, "hunt": 100, "explore": 0, "trees": 0}
        )
        result = await self.app.process_api(index, [None, 100, 0, 0])
        self.assertIn("Не сохранено", result["data"][0])
        self.assertEqual(load_weights(self.path)["farm"], 0)
        with self.assertRaises(gr.Error):
            await self.app.process_api(index, [-1, 100, 0, 0])

    async def test_timer_cannot_overwrite_unsaved_inputs(self):
        index, function = self.functions["view"]
        self.assertEqual(len(function.outputs), 4)
        self.assertFalse(any(isinstance(c, gr.Number) for c in function.outputs))
        result = await self.app.process_api(index, [])
        self.assertEqual(len(result["data"]), 4)
        self.assertIn("первого шага", result["data"][0])

    async def test_new_browser_load_reads_saved_weights_instead_of_startup_defaults(
        self,
    ):
        save_weights({"farm": 10, "hunt": 20, "explore": 70, "trees": 0}, self.path)
        loaded = []
        for index, function in self.app.fns.items():
            if len(function.outputs) == 1 and isinstance(
                function.outputs[0], gr.Number
            ):
                result = await self.app.process_api(index, [])
                loaded.append(result["data"][0])
        self.assertEqual(loaded, [10, 20, 70, 0])

    async def test_last_finished_selection_and_raw_result_remain_visible(self):
        controller = Autonomy(clock=lambda: 9, rng=Mock(), settings_path=self.path)
        controller.due = 0
        controller.rng.choices.side_effect = lambda choices, **kwargs: [choices[0]]
        controller.tick(
            self.game,
            lambda name, args: (
                {"food": 20}
                if name == "get_inventory"
                else {"status": "blocked", "reason": "No route"}
            ),
        )
        self.game["autonomy"] = controller.snapshot(self.game)
        summary, rows, history, details = self.panel.view()
        self.assertIn("Посев", summary)
        self.assertEqual(history[0][-1], "Преграда")
        self.assertEqual(rows[0][3], 0)
        self.assertEqual(
            json.loads(details)["autonomy"]["result"]["reason"], "No route"
        )
        stopped = AutonomyPanel(lambda: (self.game, False), self.path)
        self.assertIn("Агент остановлен", stopped.view()[0])

    async def test_issue_remains_visible_after_later_result_without_showing_global_pause(
        self,
    ):
        self.game["autonomy"] = {
            "phase": "waiting",
            "paused": False,
            "result": {"status": "stopped"},
            "recent_issues": [
                {
                    "activity": "trees",
                    "result": {
                        "status": "interaction_unconfirmed",
                        "reason": "Planting not confirmed at 1,63,2",
                    },
                }
            ],
        }
        summary, _, _, details = self.panel.view()
        self.assertIn("Planting not confirmed at 1,63,2", summary)
        self.assertNotIn("Самостоятельные занятия приостановлены", summary)
        self.assertEqual(
            json.loads(details)["autonomy"]["recent_issues"][0]["activity"], "trees"
        )

    async def test_tool_unavailability_is_visible_and_chance_is_zero(self):
        controller = Autonomy(clock=lambda: 9, settings_path=self.path)
        controller.enabled, controller.due = True, 0
        controller.tick(self.game, Mock(), available_tools=set())
        self.game["autonomy"] = controller.snapshot(self.game)
        summary, rows, _, _ = self.panel.view()
        self.assertIn("Tool Controls", summary)
        self.assertEqual([row[3] for row in rows], [0, 0, 0, 0])
        self.assertIn("hunt_hostiles", rows[1][5])

    async def test_pursuit_watch_and_checkpoint_are_visible_without_new_ui_outputs(
        self,
    ):
        import time

        self.game.update(
            combat_pursuit_runtime="bounded-pursuit-v1",
            combat_watch={
                "z": {
                    "type": "minecraft:zombie",
                    "reason": "Water target",
                    "until": time.time() + 30,
                }
            },
            combat_checkpoint={"status": "saved"},
        )
        summary, _, _, details = self.panel.view()
        self.assertIn("bounded-pursuit-v1", summary)
        self.assertIn("minecraft:zombie", summary)
        self.assertEqual(json.loads(details)["combat_checkpoint"]["status"], "saved")

    async def test_ranged_combat_target_and_spell_rejection_are_visible(self):
        import time

        self.game.update(
            self_defense={
                "active": True,
                "status": "out_of_reach",
                "target": {"type": "minecraft:drowned", "uuid": "d", "distance": 7},
            },
            scepter_attempt={"status": "aim_unconfirmed", "at": time.time()},
        )
        summary, _, _, details = self.panel.view()
        self.assertIn("drowned", summary)
        self.assertIn("7.0", summary)
        self.assertIn("aim_unconfirmed", summary)
        self.assertEqual(
            json.loads(details)["scepter_attempt"]["status"], "aim_unconfirmed"
        )

    async def test_command_equipment_and_observed_xp_are_visible(self):
        self.game.update(
            equipment_supply={
                "mode": "command_kit",
                "capabilities": {"give": "give", "experience": "xp"},
                "xp_reserve": 100,
                "observed": {"sword": True, "shield": True, "experience": False},
            },
            scepter_supply={"status": "equipment_preparing", "mode": "command_kit"},
            scepter_experience={"status": "xp_pending", "points_observed": 16},
        )
        summary, _, _, details = self.panel.view()
        self.assertIn("Выдача запрошена", summary)
        self.assertIn("наблюдаю 16", summary)
        self.assertIn("запас 100", summary)
        self.assertFalse(
            json.loads(details)["equipment_supply"]["observed"]["experience"]
        )
