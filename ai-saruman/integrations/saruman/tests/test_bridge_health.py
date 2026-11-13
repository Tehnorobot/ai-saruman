"""A live Python owner cannot make an old world observation fresh."""
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from py4j.java_gateway import JavaGateway, GatewayParameters, CallbackServerParameters
from saruman.bridge_health import BridgeHealth, close_bridge, native_runtime_status
from saruman.mod_goals import GoalStore
from saruman.mod_runtime import AutomationRuntime
from tests.test_mod_runtime import FakeMods


class BridgeHealthTests(unittest.TestCase):
    def setUp(self):
        self.game = {"automation_mode": "mods", "ingame": True, "alive": True,
                     "mod_runtime": {"status": "running", "in_world": True,
                                     "updated": 100, "activity": {"reason": "Защита удерживает управление"}}}
        self.health = BridgeHealth(self.game, clock=lambda: 120)

    def test_rotation_api_error_is_reported_without_losing_saved_game_facts(self):
        self.game["mod_goals"] = [{"completed": 2}]
        with self.assertLogs(level="ERROR"):
            self.health.run(Mock(side_effect=ConnectionError("getGoalRotation disconnected")), lambda: True)
        self.assertFalse(self.game["ingame"])
        self.assertIsNone(self.game["alive"])
        self.assertEqual(self.game["mod_goals"], [{"completed": 2}])
        status = native_runtime_status(self.game, True, clock=lambda: 120)
        self.assertFalse(status["world_ready"])
        self.assertEqual(status["native_status"], "unavailable")
        self.assertIn("getGoalRotation", status["native_error"])
        self.assertEqual(status["native_activity"], {})

    def test_stale_game_snapshot_is_not_refreshed_by_a_live_owner(self):
        self.game["bridge_health"] = {"updated": 120, "status": "connected"}
        value = native_runtime_status(self.game, True, clock=lambda: 120)
        self.assertTrue(value["agent_running"])
        self.assertFalse(value["native_fresh"])
        self.assertFalse(value["world_ready"])
        self.assertIsNone(value["player_alive"])

    def test_new_observation_restores_world_without_replaying_work(self):
        self.health.unavailable(ConnectionError("closed"))
        self.game["mod_runtime"] = {"status": "running", "in_world": True, "updated": 120}
        self.health.observed()
        value = native_runtime_status(self.game, True, clock=lambda: 120)
        self.assertTrue(value["world_ready"])
        self.assertTrue(value["native_fresh"])

    def test_cleanup_failure_cannot_keep_callback_port_open(self):
        gateway = JavaGateway(gateway_parameters=GatewayParameters(port=1),
                              callback_server_parameters=CallbackServerParameters(
                                  address="127.0.0.1", port=0, daemonize=True,
                                  daemonize_connections=True))
        self.addCleanup(gateway.close)
        server = gateway.get_callback_server()
        port = server.get_listening_port()
        native, water, combat = Mock(), Mock(), Mock()
        native.close.side_effect = ConnectionError("JVM exited")
        water.close.side_effect = ConnectionError("JVM exited")
        with self.assertLogs(level="ERROR"):
            close_bridge(gateway, native=native, water=water, combat=combat)
        combat.close.assert_called_once_with(gateway)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))

    def test_reconnection_does_not_issue_native_stop(self):
        native, gateway = Mock(), Mock()
        close_bridge(gateway, native=native, preserve_goals=True)
        native.close.assert_called_once_with(stop=False)
        gateway.shutdown_callback_server.assert_called_once()
        gateway.close.assert_called_once()

    def test_control_interrupt_is_not_swallowed(self):
        with self.assertRaises(KeyboardInterrupt):
            self.health.run(Mock(side_effect=KeyboardInterrupt()), lambda: True)

    def test_world_read_failure_leaves_old_connection_for_outer_rebuild(self):
        entry = Mock()
        entry.inGame.side_effect = ConnectionResetError("Minecraft crashed")
        with self.assertLogs(level="ERROR"):
            self.health.run(lambda: self.health.in_world(entry), lambda: True)
        entry.inGame.assert_called_once()
        self.assertEqual(self.game["bridge_health"]["status"], "recovering")
        self.assertFalse(native_runtime_status(self.game, True, clock=lambda: 120)["world_ready"])
        new_entry = Mock()
        new_entry.inGame.return_value = True
        self.assertTrue(self.health.in_world(new_entry))
        self.assertEqual(self.game["bridge_health"]["status"], "connected")

    def test_title_screen_is_not_a_failed_connection_or_retry_loop(self):
        entry = Mock()
        entry.inGame.return_value = False
        self.assertFalse(self.health.in_world(entry))
        self.assertEqual(self.game["bridge_health"]["status"], "connected")
        self.assertFalse(self.game["ingame"])
        entry.inGame.assert_called_once()

    def test_native_lease_releases_even_if_jvm_close_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            store = GoalStore(Path(directory)/"goals.sqlite")
            mods = FakeMods()
            first = AutomationRuntime(mods, store, clock=lambda: 100)
            mods.call = Mock(side_effect=ConnectionError("JVM exited"))
            with self.assertRaises(ConnectionError):
                first.close()
            self.assertTrue(store.lease("next-owner", 100))

    def test_reconnect_retains_intentions_and_respects_manual_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            store = GoalStore(Path(directory)/"goals.sqlite")
            mods = FakeMods()
            store.submit("trees", source="player")
            first = AutomationRuntime(mods, store, clock=lambda: 100)
            first.was_enabled = True
            first.close(stop=False)
            self.assertEqual(mods.calls, [])
            self.assertEqual(store.list()[0]["state"], "queued")
            second = AutomationRuntime(mods, store, clock=lambda: 100)
            second.was_enabled = first.was_enabled
            mods.snapshot.update(manual_stop=True, manual_stop_latched=True, defense_enabled=False)
            second.tick(self.game, enabled=True)
            self.assertEqual(self.game["mod_runtime"]["status"], "paused")
            self.assertFalse(any(op == "tree_start" for op, _ in mods.calls))

    def test_explicit_start_token_rearms_pause_once_not_on_each_heartbeat(self):
        with tempfile.TemporaryDirectory() as directory:
            store = GoalStore(Path(directory)/"goals.sqlite")
            mods = FakeMods()
            now = [100]
            runtime = AutomationRuntime(mods, store, clock=lambda: now[0])
            runtime.was_enabled = True
            request = store.submit("trees", source="operator")
            self.game["native_start_token"] = "operator-start"
            mods.snapshot.update(manual_stop=True, manual_stop_latched=True, defense_enabled=False)
            runtime.tick(self.game, enabled=True)
            self.assertTrue(any(op == "defense" and args["enabled"] for op, args in mods.calls))
            self.assertEqual(next(g for g in store.list() if g["id"] == request["id"])["state"], "running")
            mods.calls.clear()
            mods.snapshot.update(manual_stop=True, manual_stop_latched=True, defense_enabled=False)
            now[0] += 1
            runtime.tick(self.game, enabled=True)
            self.assertEqual(self.game["mod_runtime"]["status"], "paused")
            self.assertFalse(any(op == "defense" and args["enabled"] for op, args in mods.calls))
