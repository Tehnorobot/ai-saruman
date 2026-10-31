"""Only tree checkpoints survive reset; saved positions/progress are not recreated."""

import json
import unittest
from unittest.mock import Mock, patch

from saruman.tree_memory import (
    capture_tree_memory,
    prepare_resume,
    resume_paused,
    resume_request,
)
from saruman.tree_tasks import ROUTE_BLOCKED, new_task, task_config


class TreeMemoryTests(unittest.TestCase):
    def setUp(self):
        self.task = new_task(task_config(2, "grow", max_bone_meal=16))
        self.task["targets"] = [
            {"position": [1170, 63, -481], "stage": "grown", "owned": False},
            {"position": [1176, 63, -481], "stage": "planned", "owned": False},
        ]
        self.task["bone_meal_charged"] = 3
        self.task["steps"] = 7
        self.values = {
            ("saruman:dict:game", "tree_task"): json.dumps(self.task),
            ("saruman:dict:game", "tree_search"): json.dumps(
                {"visited": [[1170, 63, -481]]}
            ),
        }
        self.client = Mock()
        self.client.hget.side_effect = lambda key, field: self.values.get((key, field))
        self.client.hset.side_effect = lambda key, field, value: (
            self.values.__setitem__((key, field), value)
        )

    def test_restart_preserves_targets_doses_and_steps_and_requires_resume(self):
        memory = capture_tree_memory(self.client, "saruman:dict:game")
        restored = memory["task"]
        self.assertEqual(restored["status"], "paused")
        self.assertEqual(restored["targets"], self.task["targets"])
        resume_paused(restored)
        self.assertEqual(restored["status"], "active")
        self.assertEqual(restored["id"], self.task["id"])
        self.assertEqual(restored["steps"], 7)
        self.assertEqual(restored["bone_meal_charged"], 3)

    def test_deferred_player_goals_survive_restart_as_checkpoints_not_commands(self):
        self.values["saruman:dict:game", "deferred_goals"] = json.dumps(
            [
                {"task": self.task, "search": {"visited": [[4, 63, 5]]}},
                {"task": "invalid"},
            ]
        )
        memory = capture_tree_memory(self.client, "saruman:dict:game")
        self.assertEqual(len(memory["deferred_goals"]), 1)
        saved = memory["deferred_goals"][0]["task"]
        self.assertEqual(saved["status"], "paused")
        self.assertEqual(saved["targets"], self.task["targets"])
        self.assertEqual(saved["bone_meal_charged"], 3)

    def test_legacy_timeout_is_resumable_but_budget_failure_and_stop_are_not_auto_resumed(
        self,
    ):
        for status, reason, expected in [
            (
                "blocked",
                "Minecraft procedure timed out waiting for native work. Original request",
                "paused",
            ),
            ("blocked", "Total bone meal budget exhausted", "blocked"),
            ("cancelled", "Stopped by operator/agent", "cancelled"),
        ]:
            self.task.update(status=status, reason=reason)
            self.values["saruman:dict:game", "tree_task"] = json.dumps(self.task)
            self.assertEqual(
                capture_tree_memory(self.client, "saruman:dict:game")["task"]["status"],
                expected,
            )
        self.assertTrue(resume_request("Продолжи выращивание деревьев"))
        self.assertFalse(resume_request("Посади два дуба"))

    def test_constructor_restores_checkpoint_after_flush_without_restoring_queues(self):
        from data_flow.redis_collections import RedisDict
        from data_flow.redis_ctx_swarm import RedisCtxSwarm

        self.values["saruman:list:chat_queue", "old"] = "@get bone_meal 2"

        def initialize(swarm, populate_defaults=False):
            swarm._collections = {"game": RedisDict("game")}

        with (
            patch("data_flow.redis_backend.get_redis", return_value=self.client),
            patch("data_flow.redis_collections.get_redis", return_value=self.client),
            patch.object(RedisCtxSwarm, "_flush", side_effect=self.values.clear),
            patch.object(RedisCtxSwarm, "_init_collections", initialize),
        ):
            swarm = RedisCtxSwarm(flush_on_start=True)
            self.assertEqual(
                swarm["game"]["tree_task"]["targets"], self.task["targets"]
            )
            self.assertEqual(swarm["game"]["tree_task"]["status"], "paused")
            self.assertNotIn(("saruman:list:chat_queue", "old"), self.values)

    def test_route_failure_requires_explicit_resume_preserving_checkpoint(self):
        self.task.update(status="blocked", reason=ROUTE_BLOCKED)
        self.values["saruman:dict:game", "tree_task"] = json.dumps(self.task)
        self.assertEqual(
            capture_tree_memory(self.client, "saruman:dict:game")["task"]["status"],
            "blocked",
        )
        game = {"tree_task": self.task}
        self.assertFalse(prepare_resume(game, "Проверь инвентарь"))
        self.assertEqual(game["tree_task"]["status"], "blocked")
        prepare_resume(game, "Продолжи выращивание деревьев")
        self.assertEqual(game["tree_task"]["status"], "paused")
        self.assertEqual(game["tree_task"]["targets"], self.task["targets"])
        self.assertEqual(game["tree_task"]["steps"], 7)
        self.assertEqual(game["tree_task"]["bone_meal_charged"], 3)

    def test_invalid_snapshot_does_not_restore_arbitrary_state(self):
        self.values["saruman:dict:game", "tree_task"] = '"not a task"'
        self.assertIsNone(capture_tree_memory(self.client, "saruman:dict:game"))

    def test_old_ownership_block_can_resume_but_confirmed_replacement_cannot(self):
        old_event = {
            "event_id": 1792428820509568300,
            "observed_at": 26016.25,
            "status": "ownership_lost",
            "goal": {"item": "oak_sapling", "count": 2},
        }
        for event, expected in [
            (old_event, "paused"),
            (
                {
                    **old_event,
                    "reason": "User task identity changed",
                    "task_chain": "foreign",
                },
                "blocked",
            ),
        ]:
            self.task.update(
                status="blocked",
                reason="Native collection stopped or was replaced: "
                + str(event)
                + ". Preserve counted tree progress and targets; do not restart the collection blindly.",
            )
            self.values["saruman:dict:game", "tree_task"] = json.dumps(self.task)
            self.assertEqual(
                capture_tree_memory(self.client, "saruman:dict:game")["task"]["status"],
                expected,
            )
            game = {"tree_task": self.task}
            prepare_resume(game, "Продолжи выращивание деревьев")
            self.assertEqual(game["tree_task"]["status"], expected)
            self.assertEqual(game["tree_task"]["targets"], self.task["targets"])
            self.assertEqual(game["tree_task"]["bone_meal_charged"], 3)

    def test_stop_requires_explicit_resume_and_other_blockages_remain_blocked(self):
        self.task["status"] = "cancelled"
        game = {"tree_task": self.task, "tree_search": {"visited": [[1, 63, 2]]}}
        self.assertFalse(prepare_resume(game, "Посади два дуба"))
        self.assertEqual(game["tree_task"]["status"], "cancelled")
        self.assertTrue(prepare_resume(game, "Продолжи"))
        self.assertEqual(game["tree_task"]["status"], "paused")
        self.assertEqual(game["tree_task"]["targets"], self.task["targets"])
        game["tree_task"].update(
            status="blocked", reason="Total bone meal budget exhausted"
        )
        self.assertTrue(prepare_resume(game, "Продолжи"))
        self.assertEqual(game["tree_task"]["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
