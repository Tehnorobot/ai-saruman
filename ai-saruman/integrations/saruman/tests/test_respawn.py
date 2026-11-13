"""Death recovery over the actual native API contract, without killing a player."""

import ast
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

from saruman.navigation import NavigationGuard
from saruman.respawn import RespawnController


class StartStopTests(unittest.TestCase):
    def test_redis_defaults_include_lifecycle_and_an_explicit_stopped_state(self):
        from data_flow.redis_ctx_swarm import _DICT_DEFAULTS

        self.assertIs(_DICT_DEFAULTS["env"]["agent_enabled"], False)
        self.assertIsNone(_DICT_DEFAULTS["game"]["alive"])
        self.assertEqual(_DICT_DEFAULTS["game"]["respawn"], {})
        self.assertEqual(_DICT_DEFAULTS["game"]["survival"], {})

    def test_ui_controls_publish_respawn_permission_before_start_or_stop(self):
        # Load the actual callbacks without importing main's startup/UI side effects.
        source = Path(__file__).resolve().parents[1] / "src/main.py"
        module = ast.parse(source.read_text(encoding="utf-8"))
        callbacks = ast.Module(
            body=[
                node
                for node in module.body
                if isinstance(node, ast.FunctionDef)
                and node.name in ("agent_start", "activate_agent")
            ],
            type_ignores=[],
        )
        ctx = {"env": {"agent_enabled": False}}
        supervisor = Mock()
        supervisor.is_running.return_value = False
        seen = []
        supervisor.run.side_effect = lambda: seen.append(ctx["env"]["agent_enabled"])
        supervisor.stop.side_effect = lambda: seen.append(ctx["env"]["agent_enabled"])
        namespace = {"ctx": object(), "ctx_swarm": ctx, "Supervisor": supervisor, 'reset_pending':lambda:False}
        exec(compile(callbacks, str(source), "exec"), namespace)  # noqa: S102 - trusted local callbacks only
        namespace["agent_start"]()
        namespace["activate_agent"]()
        self.assertEqual(seen, [True, False])


class RespawnTests(unittest.TestCase):
    def test_mod_player_recovers_even_when_new_defense_task_is_active(self):
        self.game['automation_mode']='mods'
        self.entry.hasActiveTask.return_value=True
        self.tick(0);self.tick(2)
        self.entry.getGameState.return_value['self']['hp']=20
        self.assertTrue(self.tick(3))
        self.assertTrue(self.game['alive'])
        self.assertEqual(self.game['respawn']['status'],'respawned')
        self.entry.stopPathing.assert_not_called()
        self.assertTrue(self.tick(4));self.entry.respawnPlayer.assert_called_once()

    def setUp(self):
        self.now = 0
        self.entry = Mock()
        self.entry.getGameState.return_value = {
            "self": {"hp": 0, "pos": "10,63,20", "dimension": "OVERWORLD"}
        }
        self.entry.stopPathing.return_value = {"ok": True}
        self.entry.hasActiveTask.return_value = False
        self.entry.respawnPlayer.return_value = True
        self.game = {
            "ingame": True,
            "tree_task": {
                "status": "active",
                "config": {"count": 2},
                "progress": {"completed": 1},
                "targets": [{"position": [10, 63, 20]}],
            },
        }
        self.navigation = NavigationGuard()
        self.navigation.target = (20, 63, 30)
        self.navigation.collection.goal = {"item": "oak_sapling", "count": 2}
        self.hardcore = Mock(return_value=False)
        self.controller = RespawnController(self.hardcore, clock=lambda: self.now)

    def tick(self, now, enabled=True):
        self.now = now
        return self.controller.tick(
            self.entry, self.game, self.navigation, enabled=enabled
        )

    def test_live_player_with_an_open_menu_is_never_respawned(self):
        self.entry.getGameState.return_value["self"]["hp"] = 20
        self.entry.getOpenScreen.return_value = {
            "open": True,
            "type": "InventoryScreen",
        }
        self.assertTrue(self.tick(0))
        self.assertTrue(self.game["alive"])
        self.entry.respawnPlayer.assert_not_called()
        self.entry.stopPathing.assert_not_called()
        self.hardcore.assert_not_called()

    def test_death_delays_request_then_waits_for_a_fresh_living_player(self):
        saved = deepcopy(self.game["tree_task"])
        self.assertFalse(self.tick(0))
        self.assertFalse(self.game["alive"])
        self.entry.stopPathing.assert_called_once()
        self.assertIsNone(self.navigation.target)
        self.assertIsNone(self.navigation.collection.goal)
        self.assertEqual(self.game["tree_task"]["status"], "paused")
        for key in ("config", "progress", "targets"):
            self.assertEqual(self.game["tree_task"][key], saved[key])
        self.tick(1)
        self.entry.respawnPlayer.assert_not_called()
        self.tick(2)
        self.assertEqual(self.game["respawn"]["status"], "respawn_requested")
        self.assertFalse(self.game["alive"])
        self.tick(3)
        self.entry.respawnPlayer.assert_called_once()
        self.entry.getGameState.return_value["self"].update(hp=20, pos="0,64,0")
        self.assertTrue(self.tick(4))
        self.assertTrue(self.game["alive"])
        self.assertEqual(self.game["respawn"]["status"], "respawned")
        self.assertEqual(self.game["respawn"]["position"], "0,64,0")

    def test_retry_limit_requires_new_observations_and_never_spams(self):
        for now in range(31):
            self.tick(now)
        self.assertEqual(self.entry.respawnPlayer.call_count, 3)
        self.entry.stopPathing.assert_called_once()
        self.assertEqual(self.game["respawn"]["status"], "retry_wait")
        self.assertFalse(self.game["alive"])
        self.tick(76)
        self.assertEqual(self.entry.respawnPlayer.call_count, 3)
        self.tick(77)
        self.assertEqual(self.entry.respawnPlayer.call_count, 4)

    def test_stop_prevents_recovery_and_start_resumes_it(self):
        self.tick(0, enabled=False)
        self.tick(10, enabled=False)
        self.entry.stopPathing.assert_not_called()
        self.entry.respawnPlayer.assert_not_called()
        self.assertEqual(self.game["respawn"]["status"], "paused")
        self.tick(11)
        self.entry.respawnPlayer.assert_called_once()

    def test_stopping_after_a_request_prevents_further_requests(self):
        self.tick(0)
        self.tick(2)
        self.tick(7, enabled=False)
        self.tick(20, enabled=False)
        self.entry.respawnPlayer.assert_called_once()

    def test_hardcore_is_not_treated_as_normal_respawn(self):
        self.hardcore.return_value = True
        for now in (0, 5, 15):
            self.tick(now)
        self.entry.respawnPlayer.assert_not_called()
        self.assertEqual(self.game["respawn"]["status"], "blocked")

    def test_missing_disconnected_or_invalid_health_cannot_trigger_respawn(self):
        for snapshot in (
            {},
            {"self": {}},
            {"self": {"hp": float("nan")}},
            {"self": {"hp": True}},
            {"inGame": False},
            None,
        ):
            with self.subTest(snapshot=snapshot):
                self.entry.getGameState.return_value = snapshot
                self.assertFalse(self.tick(self.now + 1))
                self.assertIsNone(self.game["alive"])
        self.entry.respawnPlayer.assert_not_called()

    def test_native_timeout_is_not_replayed_immediately(self):
        self.entry.respawnPlayer.side_effect = TimeoutError("unknown result")
        self.tick(0)
        self.tick(2)
        self.tick(2.5)
        self.tick(3)
        self.entry.respawnPlayer.assert_called_once()
        self.assertFalse(self.game["alive"])

    def test_old_task_must_end_before_world_actions_resume(self):
        self.tick(0)
        self.tick(2)
        self.entry.getGameState.return_value["self"]["hp"] = 20
        self.entry.hasActiveTask.return_value = True
        self.assertFalse(self.tick(3))
        self.assertEqual(self.game["respawn"]["status"], "stopping_old_task")
        self.entry.hasActiveTask.return_value = False
        self.assertTrue(self.tick(4))

    def test_manual_respawn_while_stopped_does_not_send_a_request(self):
        self.tick(0, enabled=False)
        self.entry.getGameState.return_value["self"]["hp"] = 20
        self.assertTrue(self.tick(3, enabled=False))
        self.entry.stopPathing.assert_not_called()
        self.entry.respawnPlayer.assert_not_called()

    def test_unknown_stop_does_not_block_respawn_but_still_blocks_world_actions(self):
        self.entry.stopPathing.side_effect = TimeoutError("unknown result")
        self.entry.hasActiveTask.return_value = True
        self.tick(0)
        self.tick(5)
        self.entry.stopPathing.assert_called_once()
        self.entry.respawnPlayer.assert_called_once()
        self.entry.getGameState.return_value["self"]["hp"] = 20
        self.assertFalse(self.tick(6))
        self.entry.hasActiveTask.return_value = False
        self.assertTrue(self.tick(7))

    def test_second_death_gets_a_new_identity_and_attempt_budget(self):
        self.tick(0)
        self.tick(2)
        previous = self.game["respawn"]["death_id"]
        self.entry.getGameState.return_value["self"]["hp"] = 20
        self.tick(3)
        self.entry.getGameState.return_value["self"]["hp"] = 0
        self.tick(4)
        self.assertNotEqual(self.game["respawn"]["death_id"], previous)
        self.assertEqual(self.game["respawn"]["attempts"], 0)
        self.tick(6)
        self.assertEqual(self.entry.respawnPlayer.call_count, 2)
