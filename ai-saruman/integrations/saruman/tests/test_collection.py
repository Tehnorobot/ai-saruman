"""Long collection, confirmed cancellation and parent continuation without a live world."""

import unittest
from unittest.mock import MagicMock, Mock, patch

from saruman.collection import CollectionGuard, read_user_task
from saruman.navigation import NavigationGuard
from saruman.procedures import ProcedureContinuation


class CollectionTests(unittest.TestCase):
    def setUp(self):
        clock = patch("saruman.collection.time.monotonic", return_value=0)
        self.clock = clock.start()
        self.addCleanup(clock.stop)
        self.entry = Mock()
        self.entry.getInventoryFull.return_value = {"ok": True, "slots": []}
        self.entry.stopPathing.return_value = {"ok": True}
        self.guard = CollectionGuard()
        self.game = {}
        self.chain = "Main task: <Craft 2x2 Task Recipe{minecraft:bone_meal x 2}: [[bone_meal] x 2]>"
        self.guard.start(self.entry, "@get bone_meal 2", self.game)

    def tick(
        self,
        seconds,
        position="0,63,0",
        busy=True,
        chain=None,
        state=None,
        user_task=None,
    ):
        self.clock.return_value = seconds
        self.guard.tick(
            self.entry, self.game, position, busy, chain or self.chain, state, user_task
        )

    def test_eight_minute_progressing_collection_is_not_stopped_at_three_minutes(self):
        continuation = ProcedureContinuation("Grow 2 trees")
        with patch("saruman.procedures.time.monotonic", return_value=0):
            continuation.observe(
                "manage_trees",
                {},
                {
                    "task_id": "job",
                    "status": "needs_materials",
                    "next_action": {
                        "tool": "agent_action",
                        "arguments": {"action": "@get bone_meal 2"},
                    },
                },
            )
            continuation.observe("agent_action", {"action": "@get bone_meal 2"}, True)
        for seconds in range(3, 480, 30):
            self.tick(seconds, f"{seconds // 10},63,0")
            with patch("saruman.procedures.time.monotonic", return_value=seconds):
                self.assertIsNone(continuation.poll(True, self.game["collection"]))
        self.tick(480, "48,63,0", busy=False)
        with patch("saruman.procedures.time.monotonic", return_value=480):
            self.assertIn(
                "Resume manage_trees", continuation.poll(False, self.game["collection"])
            )
        self.assertFalse(continuation.done)
        self.entry.stopPathing.assert_not_called()

    def test_circling_stalls_then_cancels_once_and_confirms_idle(self):
        self.tick(3)
        self.tick(20, "2,63,0")
        self.tick(40)
        self.tick(100, "2,63,0")
        self.tick(141)
        self.assertEqual(self.game["collection"]["status"], "stopping")
        self.tick(142)
        self.entry.stopPathing.assert_called_once()
        self.tick(143, busy=False)
        self.assertEqual(self.game["collection"]["status"], "blocked")
        self.assertTrue(self.game["collection"]["native_stopped"])

    def test_absolute_limit_even_with_new_spatial_progress(self):
        for seconds in range(3, 900, 30):
            self.tick(seconds, f"{seconds},63,0")
        self.tick(901, "901,63,0")
        self.entry.stopPathing.assert_called_once()
        self.assertEqual(self.game["collection"]["status"], "stopping")

    def test_foreign_task_is_not_cancelled_and_duplicate_does_not_restart(self):
        with self.assertRaises(ValueError):
            self.guard.start(self.entry, "@get bone_meal 2", self.game)
        self.tick(3, user_task={"id": 11, "text": "<Craft bone_meal>"})
        self.tick(
            200,
            chain="Main task: <Getting to block class_2338{x=1,y=63,z=0}>",
            user_task={"id": 22, "text": "<Getting to block>"},
        )
        self.assertEqual(self.game["collection"]["status"], "ownership_lost")
        self.entry.stopPathing.assert_not_called()

    def test_scheduler_preemption_keeps_root_and_parent_then_resumes_collection(self):
        root = {"id": 11, "text": "<Craft bone_meal>"}
        continuation = ProcedureContinuation("Grow 2 trees")
        continuation.observe(
            "manage_trees", {}, {"task_id": "job", "status": "needs_materials"}
        )
        continuation.observe("agent_action", {"action": "@get bone_meal 2"}, True)
        self.tick(3, user_task=root)
        self.tick(
            18,
            chain="Current Baritone executor task list (Food task chain)\n1. Main task: <Eat food>",
            user_task=root,
        )
        self.assertEqual(self.game["collection"]["status"], "suspended")
        self.assertIsNone(continuation.poll(True, self.game["collection"]))
        self.tick(200, user_task=root)
        self.assertEqual(self.game["collection"]["status"], "running")
        self.assertIsNone(continuation.poll(True, self.game["collection"]))
        self.tick(201, busy=False)
        self.assertIn(
            "Resume manage_trees", continuation.poll(False, self.game["collection"])
        )
        self.assertFalse(continuation.done)
        self.entry.stopPathing.assert_not_called()

    def test_transient_missing_chain_is_observed_instead_of_assumed_replaced(self):
        self.tick(3)
        self.tick(18, chain="Error when getting tasks! Something is broken!")
        self.assertEqual(self.game["collection"]["status"], "observing")
        self.tick(20)
        self.assertEqual(self.game["collection"]["status"], "running")
        self.entry.stopPathing.assert_not_called()

    def test_unknown_ownership_has_bounded_wait_and_never_cancels(self):
        self.tick(3, chain="No selected tasks", user_task={})
        self.tick(34, chain="No selected tasks", user_task={})
        self.assertEqual(self.game["collection"]["status"], "ownership_unconfirmed")
        self.assertEqual(self.game["collection"]["task_chain"], "No selected tasks")
        self.assertIsNone(self.guard.goal)
        self.entry.stopPathing.assert_not_called()

    def test_replacement_with_same_item_is_detected_by_identity_not_text(self):
        self.tick(3, user_task={"id": 11, "text": "<Craft bone_meal>"})
        self.tick(6, user_task={"id": 22, "text": "<Craft bone_meal>"})
        self.assertEqual(self.game["collection"]["status"], "ownership_lost")
        self.assertEqual(
            self.game["collection"]["reason"], "User task identity changed"
        )
        self.entry.stopPathing.assert_not_called()

    def test_preempted_collection_hits_absolute_limit_without_cancelling_foreign_chain(
        self,
    ):
        root = {"id": 11, "text": "<Craft bone_meal>"}
        self.tick(3, user_task=root)
        self.tick(901, chain="Main task: <Defend from mob>", user_task=root)
        self.assertEqual(self.game["collection"]["status"], "ownership_unconfirmed")
        self.entry.stopPathing.assert_not_called()

    def test_root_read_failure_does_not_fall_back_to_cancelling_matching_text(self):
        self.tick(3, user_task={"id": 11, "text": "<Craft bone_meal>"})
        self.tick(200)
        self.assertEqual(self.game["collection"]["status"], "observing")
        self.tick(231)
        self.assertEqual(self.game["collection"]["status"], "ownership_unconfirmed")
        self.entry.stopPathing.assert_not_called()

    def test_user_root_reader_uses_public_api_and_handles_idle_and_old_mod(self):
        gateway = Mock()
        root = MagicMock()
        root.__str__.return_value = "<Mine And Collect: [[oak_sapling] x 2]>"
        chain = gateway.jvm.adris.altoclef.AltoClef.getInstance.return_value.getUserTaskChain.return_value
        chain.getCurrentTask.return_value = root
        gateway.jvm.java.lang.System.identityHashCode.return_value = 42
        self.assertEqual(read_user_task(gateway), {"id": 42, "text": str(root)})
        chain.getCurrentTask.return_value = None
        self.assertEqual(read_user_task(gateway), {})
        chain.getCurrentTask.side_effect = TypeError("Older API")
        self.assertIsNone(read_user_task(gateway))

    def test_inventory_and_mining_are_progress_but_missing_inventory_is_not(self):
        self.tick(3)
        self.entry.getInventoryFull.return_value["slots"] = [
            {"item": "minecraft:bone", "count": 1}
        ]
        self.tick(110)
        self.tick(220, state={"msSinceBreakProgress": 100})
        self.entry.getInventoryFull.side_effect = TimeoutError("unavailable")
        self.tick(230)
        self.entry.stopPathing.assert_not_called()
        self.tick(341)
        self.entry.stopPathing.assert_called_once()

    def test_stop_uncertainty_is_not_replayed_or_replaced_by_another_get(self):
        self.tick(3)
        self.entry.stopPathing.side_effect = TimeoutError("reply lost")
        self.tick(124)
        self.tick(130)
        self.assertEqual(self.game["collection"]["status"], "stop_failed")
        self.entry.stopPathing.assert_called_once()
        navigation = NavigationGuard()
        self.entry.hasActiveTask.return_value = True
        with self.assertRaisesRegex(ValueError, "cancellation"):
            navigation.send(self.entry, "@get oak_sapling 1", self.game)

    def test_dispatch_and_snapshot_integration(self):
        entry = Mock()
        entry.getInventoryFull.return_value = {"ok": True, "slots": []}
        navigation, game = NavigationGuard(), {}
        navigation.send(entry, "@get bone_meal 2", game)
        self.clock.return_value = 10
        navigation.tick(entry, game, "0,63,0", True, self.chain)
        self.assertEqual(game["collection"]["status"], "running")
        entry.RunInnerCommand.assert_called_once_with("@get bone_meal 2")

    def test_single_tree_and_namespaced_obtain_also_wait_for_guarded_collection(self):
        continuation = ProcedureContinuation("Grow a tree")
        continuation.observe(
            "obtain_item",
            {"item": "minecraft:bone_meal", "count": 2},
            "Queued obtaining/crafting",
        )
        self.tick(200)
        self.assertIsNone(continuation.poll(True, self.game["collection"]))
        self.assertFalse(continuation.done)
        self.tick(201, busy=False)
        self.assertIn(
            "previously requested", continuation.poll(False, self.game["collection"])
        )

    def test_stale_bridge_does_not_cause_unbounded_waiting(self):
        continuation = ProcedureContinuation("Grow a tree")
        continuation.observe(
            "obtain_item",
            {"item": "bone_meal", "count": 2},
            "Queued obtaining/crafting",
        )
        self.tick(10)
        self.clock.return_value = 200
        self.assertIn(
            "status is stale", continuation.poll(True, self.game["collection"])
        )
        self.assertTrue(continuation.done)
        self.entry.stopPathing.assert_not_called()


if __name__ == "__main__":
    unittest.main()
