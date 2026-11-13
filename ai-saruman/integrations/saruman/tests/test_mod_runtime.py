"""Protocol/lifecycle tests. These do not claim Minecraft gameplay acceptance."""

import copy
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from saruman.mod_goals import GoalStore, submit_command
from saruman.mod_runtime import AutomationRuntime
from saruman.mod_supply import ModSupply


class FakeMods:
    def __init__(self):
        self.snapshot = {
            "status": "ready",
            "world": "world1",
            "in_world": True,
            "defense_enabled": True,
            "defense_busy": False,
            "native_busy": False,
            "tree": {},
            "defense": {"owner": "NONE", "missingItems": []},
        }
        self.calls, self.receipts = [], {}
        self.lose_next = False

    def call(self, operation, **args):
        self.calls.append((operation, args))
        if operation == "snapshot":
            return copy.deepcopy(self.snapshot)
        if operation == "defense":
            self.snapshot["defense_enabled"] = args["enabled"]
            if args["enabled"]:
                self.snapshot.update(manual_stop=False, manual_stop_latched=False, stop_kind="none")
        if operation == "tree_start":
            request = args["request_id"]
            if request not in self.receipts:
                self.receipts[request] = {"status": "started", "run_id": request}
                self.snapshot["tree"] = {"run_id": request, "isFinished": False}
                self.snapshot["native_busy"] = True
            if self.lose_next:
                self.lose_next = False
                raise TimeoutError("delivery happened, reply lost")
            return self.receipts[request]
        if operation in {"cancel", "stop_command"}:
            self.snapshot["native_busy"] = False
        if operation == "command":
            self.snapshot["native_busy"] = True
            request = args["request_id"]
            self.receipts.setdefault(request, {"status": "sent"})
            self.snapshot["command"] = {"run_id": request, "current": True, "finished": False}
            if self.lose_next:
                self.lose_next = False
                raise TimeoutError("command delivered, reply lost")
            return self.receipts[request]
        if operation == "tree_resume":
            self.snapshot["tree"]["isFinished"] = False
            self.snapshot["native_busy"] = True
            return {"status": "resumed"}
        return {"status": "applied"}

    def finish(self, success=True):
        self.snapshot["native_busy"] = False
        self.snapshot["tree"].update(
            isFinished=True,
            success=success,
            result="grown" if success else "Отменено: задача UnionClef прервана",
            outcome="grown" if success else "cancelled",
            interruption="" if success else "scheduler",
        )


class NativeRuntimeTests(unittest.TestCase):
    def test_cancellation_rejects_unknown_id_and_resolves_real_run_id(self):
        from agent.tools import tools_config
        from agent.tools.mod_tools import cancel_mod_goal

        goal = self.goal()
        goal = self.store.update(goal, state="running", run_id="native-run:0")
        context = {
            "game": {
                "mod_player_context": {"id": "stop-request", "until": time.time() + 60}
            }
        }
        with (
            patch.object(tools_config, "ctx_swarm", context),
            patch("agent.tools.mod_tools.GoalStore", return_value=self.store),
        ):
            self.assertEqual(cancel_mod_goal("missing")["status"], "rejected")
            self.assertEqual(self.read(goal)["state"], "running")
            result = cancel_mod_goal("native-run:0")
        self.assertEqual(result["goal_ids"], [goal["id"]])
        self.assertEqual(self.read(goal)["state"], "cancel_requested")

    def test_stop_tree_growing_disables_future_trees_and_cancels_only_trees(self):
        from agent.tools import tools_config
        from agent.tools.mod_tools import stop_tree_growing

        player_tree = self.goal("player")
        background_tree = self.goal("planner")
        follow = self.store.submit(
            "command", source="player", command="@follow Alex", request_id="follow"
        )
        context = {
            "game": {
                "mod_player_context": {"id": "stop-request", "until": time.time() + 60}
            }
        }
        with (
            patch.object(tools_config, "ctx_swarm", context),
            patch("agent.tools.mod_tools.GoalStore", return_value=self.store),
            patch(
                "agent.tools.mod_tools.load_weights",
                return_value={"trees": 2, "farm": 6},
            ),
            patch("agent.tools.mod_tools.save_weights") as save,
        ):
            result = stop_tree_growing()
        save.assert_called_once_with({"trees": 0, "farm": 6})
        self.assertFalse(result["background_trees_enabled"])
        self.assertEqual(
            set(result["goal_ids"]), {player_tree["id"], background_tree["id"]}
        )
        self.assertEqual(self.read(player_tree)["state"], "cancel_requested")
        self.assertEqual(self.read(follow)["state"], "queued")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = GoalStore(Path(self.temp.name) / "goals.sqlite")
        self.adapter = FakeMods()
        self.now = 1000
        self.game = {"automation_mode": "mods", "alive": True}
        self.events = []
        self.runtime = AutomationRuntime(
            self.adapter, self.store, clock=lambda: self.now, notify=self.events.append
        )
        self.environment = patch.dict("os.environ", SARUMAN_AUTONOMY_ENABLED="false")
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def tick(self, enabled=True):
        self.now += 1
        self.runtime.tick(self.game, enabled=enabled)

    def goal(self, source="player", count=1):
        return self.store.submit(
            "trees", source=source, count=count, request_id=f"request:{source}:{count}"
        )

    def read(self, goal):
        return next(g for g in self.store.list() if g["id"] == goal["id"])

    def test_counted_trees_only_confirmed_success_advances(self):
        goal = self.goal(count=2)
        self.tick()
        self.assertEqual(self.read(goal)["completed"], 0)
        self.adapter.finish()
        self.tick()
        self.assertEqual(self.read(goal)["completed"], 1)
        self.tick()
        self.adapter.finish()
        self.tick()
        self.assertEqual(self.read(goal)["state"], "completed")
        self.assertEqual(self.read(goal)["completed"], 2)
        starts = [a["request_id"] for op, a in self.adapter.calls if op == "tree_start"]
        self.assertEqual(len(set(starts)), 2)
        self.assertEqual(len(self.events), 1)

    def test_failure_does_not_invent_growth_or_retry(self):
        goal = self.goal(count=2)
        self.tick()
        self.adapter.finish(False)
        self.tick()
        self.tick()
        self.assertEqual(self.read(goal)["state"], "failed")
        self.assertEqual(self.read(goal)["completed"], 0)
        self.assertEqual(sum(op == "tree_start" for op, _ in self.adapter.calls), 1)

    def test_player_preempts_planner_and_preserves_observed_count(self):
        background = self.goal("planner", 2)
        self.tick()
        self.adapter.finish()
        self.goal("player")
        self.tick()
        self.assertEqual(self.read(background)["state"], "cancelled")
        self.assertEqual(self.read(background)["completed"], 1)
        self.assertEqual(self.read(background)["reason"], "Player priority")

    def test_player_priority_before_background_even_when_submitted_later(self):
        self.goal("planner")
        player = self.goal("player")
        self.tick()
        self.assertEqual(self.read(player)["state"], "running")

    def test_defense_preempts_without_llm_and_resumes_same_run(self):
        goal = self.goal()
        self.tick()
        run = self.read(goal)["run_id"]
        self.adapter.snapshot["defense_busy"] = True
        self.adapter.finish(False)
        self.tick()
        self.assertEqual(self.read(goal)["state"], "interrupted")
        self.adapter.snapshot["defense_busy"] = False
        self.tick()
        self.assertEqual(self.read(goal)["run_id"], run)
        self.assertEqual(self.read(goal)["state"], "running")
        self.assertEqual(sum(op == "tree_start" for op, _ in self.adapter.calls), 1)
        self.assertFalse(any(op == "cancel" for op, _ in self.adapter.calls))

    def test_lost_resume_reply_and_second_interruption_keep_original_run(self):
        goal = self.goal()
        self.tick()
        original = self.read(goal)["run_id"]
        native_call = self.adapter.call
        lost = [False]

        def transport(operation, **args):
            result = native_call(operation, **args)
            if operation == "tree_resume" and not lost[0]:
                lost[0] = True
                raise TimeoutError("resume delivered; reply lost")
            return result

        self.adapter.call = transport
        for _ in range(2):
            self.adapter.snapshot["defense_busy"] = True
            self.adapter.finish(False)
            self.tick()
            self.adapter.snapshot["defense_busy"] = False
            self.tick()
            self.tick()
        self.assertEqual(self.read(goal)["run_id"], original)
        self.assertEqual(self.read(goal)["resumes"], 2)
        self.assertEqual(self.read(goal)["state"], "running")

    def test_new_treegrow_failure_after_combat_keeps_reason_without_resume(self):
        goal = self.goal()
        self.tick()
        self.adapter.snapshot["defense_busy"] = True
        self.tick()
        self.adapter.snapshot["defense_busy"] = False
        self.adapter.finish(False)
        self.adapter.snapshot["tree"].update(
            outcome="failed", result="Неудача: саженец не найден в радиусе 64 блоков"
        )
        self.tick()
        self.assertEqual(self.read(goal)["state"], "failed")
        self.assertIn("саженец не найден", self.read(goal)["reason"])
        self.assertFalse(any(op == "tree_resume" for op, _ in self.adapter.calls))

    def test_new_goal_can_be_queued_while_agent_is_stopped(self):
        self.tick(False)
        goal = self.goal()
        self.tick(False)
        self.assertEqual(self.read(goal)["state"], "queued")
        self.tick(True)
        self.assertEqual(self.read(goal)["state"], "running")

    def test_world_change_prevents_replay(self):
        goal = self.goal()
        self.tick()
        self.adapter.snapshot.update(world="world2", tree={})
        self.tick()
        self.assertEqual(self.read(goal)["state"], "uncertain")

    def test_cancel_from_old_world_does_not_block_next_player_goal(self):
        old = self.store.submit(
            "command", source="player", command="@follow Alex", request_id="old"
        )
        old = self.store.update(
            old, state="running", world="old-world", run_id="old-run"
        )
        self.store.cancel(old["id"])
        following = self.store.submit(
            "command", source="player", command="@follow Alex", request_id="new"
        )
        self.tick()
        self.assertEqual(self.read(old)["state"], "cancelled")
        self.assertEqual(self.read(following)["state"], "running")
        self.assertFalse(
            any(op in {"cancel", "stop_command"} for op, _ in self.adapter.calls)
        )
        self.assertEqual(sum(op == "command" for op, _ in self.adapter.calls), 1)

    def test_cancel_from_old_world_closes_even_while_defense_owns_body(self):
        old = self.goal()
        old = self.store.update(
            old, state="running", world="old-world", run_id="old-run"
        )
        self.store.cancel(old["id"])
        self.adapter.snapshot["defense_busy"] = True
        self.tick()
        self.assertEqual(self.read(old)["state"], "cancelled")
        self.assertFalse(
            any(
                op in {"cancel", "stop_command", "tree_start"}
                for op, _ in self.adapter.calls
            )
        )

    def test_lost_start_reply_reconciles_without_duplicate(self):
        goal = self.goal()
        self.adapter.lose_next = True
        self.tick()
        self.assertEqual(self.read(goal)["state"], "starting")
        self.tick()
        self.adapter.finish()
        self.tick()
        self.assertEqual(self.read(goal)["completed"], 1)
        self.assertEqual(len(self.adapter.receipts), 1)

    def test_missing_run_is_uncertain_without_replanting(self):
        goal = self.goal()
        self.tick()
        self.adapter.snapshot["tree"] = {}
        self.tick()
        self.assertEqual(self.read(goal)["state"], "uncertain")

    def test_stop_cancels_and_disables_defense(self):
        goal = self.goal()
        self.tick()
        self.tick(False)
        self.assertEqual(self.read(goal)["state"], "cancelled")
        self.assertEqual(self.read(goal)["reason"], "Automation stopped")
        self.assertFalse(self.adapter.snapshot["defense_enabled"])

    def test_native_manual_stop_cancels_instead_of_resuming(self):
        goal = self.goal()
        self.tick()
        self.adapter.snapshot["manual_stop"] = True
        self.tick()
        self.assertEqual(self.read(goal)["state"], "cancelled")
        self.assertEqual(self.read(goal)["reason"], "Native task stop")
        self.assertFalse(any(op == "tree_resume" for op, _ in self.adapter.calls))

    def test_cancel_reason_survives_defense_and_another_player_goal(self):
        goal = self.goal()
        self.tick()
        self.adapter.snapshot["defense_busy"] = True
        self.store.cancel(goal["id"], reason="Explicit operator cancellation")
        self.goal("player")
        self.tick()
        self.assertEqual(self.read(goal)["state"], "cancel_requested")
        self.adapter.snapshot["defense_busy"] = False
        self.tick()
        self.assertEqual(self.read(goal)["state"], "cancelled")
        self.assertEqual(self.read(goal)["reason"], "Explicit operator cancellation")

    def test_native_speech_uses_database_edits_and_publishes_recent_lines(self):
        self.goal()
        self.tick()
        self.game["action_voice_records"] = [
            {
                "id": "custom.plant",
                "kind": "phrase",
                "event": "plant_sapling",
                "text": "Пусть укореняется. Мне по нраву этот замысел.",
                "enabled": True,
            }
        ]
        self.adapter.snapshot["tree"].update(planted=True, site="one")
        self.runtime.announce = Mock()
        self.tick()
        self.runtime.announce.assert_called_once_with(
            self.game["action_voice_records"][0]["text"], "neutral"
        )
        self.assertEqual(self.game["mod_commentary"]["source"], "postgresql")
        self.assertEqual(self.game["mod_commentary"]["recent"][0]["id"], "custom.plant")

    def test_manual_cancel_wins_over_late_observation(self):
        goal = self.goal()
        self.store.cancel(goal["id"])
        self.store.update(goal, state="running", completed=1)
        self.assertEqual(self.read(goal)["state"], "cancel_requested")

    def test_restart_reads_saved_goal_in_same_native_world(self):
        goal = self.goal(count=2)
        self.tick()
        self.store.lease(self.runtime.owner, self.now, ttl=0)
        self.runtime = AutomationRuntime(
            self.adapter, GoalStore(self.store.path), clock=lambda: self.now
        )
        self.adapter.finish()
        self.tick()
        self.assertEqual(self.read(goal)["completed"], 1)

    def test_second_runtime_cannot_control_same_database(self):
        self.goal()
        self.tick()
        other = AutomationRuntime(self.adapter, self.store, clock=lambda: self.now)
        before = len(self.adapter.calls)
        other.tick(self.game, enabled=True)
        self.assertEqual(len(self.adapter.calls), before)

    def test_no_model_or_commands_in_planner(self):
        with self.assertRaises(ValueError):
            self.store.submit("command", source="planner", command="@goto 1 2 3")

    def test_idempotent_submissions(self):
        self.assertEqual(self.goal()["id"], self.goal()["id"])
        self.assertEqual(len(self.store.list()), 1)

    def test_dead_player_does_not_start_work(self):
        goal = self.goal()
        self.game["alive"] = False
        self.tick()
        self.assertEqual(self.read(goal)["state"], "queued")

    def test_death_during_defense_never_resumes_old_tree(self):
        goal = self.goal()
        self.tick()
        self.adapter.snapshot["defense_busy"] = True
        self.adapter.finish(False)
        self.tick()
        self.adapter.snapshot["defense_busy"] = False
        self.game["alive"] = False
        self.tick()
        self.game["alive"] = True
        self.tick()
        self.assertEqual(self.read(goal)["state"], "failed")
        self.assertFalse(any(op == "tree_resume" for op, _ in self.adapter.calls))

    def test_command_chains_are_not_admitted(self):
        for command in ("@get dirt 1;@attack Villager", "@goto 1 2 3\r@stop"):
            with self.assertRaises(ValueError):
                self.store.submit("command", source="player", command=command)

    def test_command_waits_for_defense_and_does_not_replay_after_interruption(self):
        goal = self.store.submit("command", source="player", command="@get bed 1")
        self.adapter.snapshot["defense_busy"] = True
        self.tick()
        self.assertEqual(self.read(goal)["state"], "queued")
        self.adapter.snapshot["defense_busy"] = False
        self.tick()
        self.adapter.snapshot["defense_busy"] = True
        self.tick()
        self.adapter.snapshot.update(defense_busy=False, native_busy=False)
        self.tick()
        self.assertEqual(self.read(goal)["state"], "uncertain")
        self.assertEqual(sum(op == "command" for op, _ in self.adapter.calls), 1)

    def test_tool_controls_disable_background_skill(self):
        with patch.dict("os.environ", SARUMAN_AUTONOMY_ENABLED="true"):
            self.game["mod_tool_controls"] = {"grow_trees": False}
            self.tick()
        self.assertEqual(self.store.list(), [])

    def test_chat_reservation_preempts_without_waiting_for_llm(self):
        goal = self.goal("planner")
        self.tick()
        self.game["mod_player_pending_until"] = self.now + 30
        self.tick()
        self.assertEqual(self.read(goal)["state"], "cancelled")

    def test_unavailable_native_snapshot_fails_closed(self):
        goal = self.goal()
        self.adapter.snapshot = {"status": "error", "reason": "wrong version"}
        self.tick()
        self.assertEqual(self.game["mod_runtime"]["status"], "unavailable")
        self.assertEqual(self.read(goal)["state"], "queued")

    def test_command_rejected_without_player_context(self):
        self.assertEqual(submit_command(self.game, "@goto 1 2 3")["status"], "rejected")

    def test_invalid_counts(self):
        for count in (0, 101, True, 1.5):
            with self.assertRaises(ValueError):
                self.goal(count=count)

    def test_supply_is_bounded_and_potion_has_healing_components(self):
        gateway = Mock()
        supplier = ModSupply(gateway, self.store, clock=lambda: self.now)
        snap = copy.deepcopy(self.adapter.snapshot)
        snap["defense"]["missingItems"] = [{"item": "minecraft:potion", "missing": 2}]
        with patch(
            "saruman.mod_supply.command_capabilities", return_value={"give": "give"}
        ):
            supplier.tick(self.game, snap)
            self.now += 5
            supplier.tick(self.game, snap)
        gateway.entry_point.ChatMessage.assert_called_once()
        self.assertIn(
            'potion:"minecraft:healing"',
            gateway.entry_point.ChatMessage.call_args[0][0],
        )

    def test_supply_never_blocks_or_sends_during_defense(self):
        gateway = Mock()
        supplier = ModSupply(gateway, self.store, clock=lambda: self.now)
        snap = copy.deepcopy(self.adapter.snapshot)
        snap["defense_busy"] = True
        supplier.tick(self.game, snap)
        gateway.entry_point.ChatMessage.assert_not_called()

    def test_native_supplier_does_not_get_duplicate_python_give(self):
        gateway = Mock()
        supplier = ModSupply(gateway, self.store, clock=lambda: self.now)
        snap = copy.deepcopy(self.adapter.snapshot)
        snap["native_supply"] = True
        snap["defense"]["missingItems"] = [{"item": "minecraft:iron_sword", "missing": 1}]
        with patch("saruman.mod_supply.command_capabilities") as capabilities:
            supplier.tick(self.game, snap)
        capabilities.assert_not_called()
        gateway.entry_point.ChatMessage.assert_not_called()
        self.assertEqual(self.game["mod_supply"]["status"], "owned_by_addon")

    def test_native_manual_stop_stays_paused_until_an_explicit_new_start(self):
        self.tick()
        self.adapter.calls.clear()
        self.adapter.snapshot.update(defense_enabled=False, manual_stop=True, manual_stop_latched=True)
        self.tick()
        self.tick()
        self.assertFalse(any(op == "defense" and args["enabled"] for op, args in self.adapter.calls))
        self.assertEqual(self.game["mod_runtime"]["status"], "paused")
        self.tick(enabled=False)
        self.tick(enabled=True)
        self.assertTrue(any(op == "defense" and args["enabled"] for op, args in self.adapter.calls))
        self.assertEqual(self.game["mod_runtime"]["status"], "running")

    def test_latched_native_stop_does_not_start_more_background_work(self):
        self.tick()
        self.adapter.calls.clear()
        self.adapter.snapshot.update(defense_enabled=False, manual_stop=True, manual_stop_latched=True)
        with patch.dict("os.environ", SARUMAN_AUTONOMY_ENABLED="true"), patch("saruman.mod_runtime.load_weights", return_value={"trees": 1}):
            self.tick()
        self.assertFalse(any(op == "tree_start" for op, _ in self.adapter.calls))
        self.assertEqual(self.store.list(active=True), [])


class NativeRoutingTests(unittest.TestCase):
    def setUp(self):
        from agent.tools import tools_config

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.environment = patch.dict(
            "os.environ", SARUMAN_GOALS_DB=str(Path(self.temp.name) / "queue.sqlite")
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.game = {
            "automation_mode": "mods",
            "alive": True,
            "mod_player_context": {
                "id": "turn",
                "source": "player",
                "replying": True,
                "until": time.time() + 60,
            },
        }
        self.context_patch = patch.object(
            tools_config, "ctx_swarm", {"game": self.game}
        )
        self.context_patch.start()
        self.addCleanup(self.context_patch.stop)

    def test_accepted_tree_request_submits_one_goal_without_legacy_actions(self):
        from agent.multi.chat_agent import _execute_tool_call
        from agent.tools.mod_tools import grow_trees

        registry = {"grow_trees": {"function": grow_trees}}
        result = _execute_tool_call(
            registry, "grow_trees", {"count": 2}, game=self.game
        )
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(GoalStore().list()[0]["count"], 2)

    def test_old_tree_or_combat_tools_are_rejected_even_if_explicitly_enabled(self):
        from agent.multi.chat_agent import _execute_tool_call, _get_available_tools

        forbidden = Mock()
        registry = {
            "manage_trees": {
                "function": forbidden,
                "disabled": False,
                "tool": object(),
            },
            "agent_action": {"function": Mock(), "tool": "command-tool"},
        }
        result = _execute_tool_call(
            registry, "manage_trees", {"count": 1}, game=self.game
        )
        self.assertEqual(result["status"], "rejected")
        forbidden.assert_not_called()
        self.assertEqual(
            _get_available_tools(registry, game=self.game), ["command-tool"]
        )

    def test_idle_llm_cannot_turn_recent_player_context_into_new_commands(self):
        from agent.multi.chat_agent import _execute_tool_call

        self.game["mod_player_context"]["replying"] = False
        function = Mock()
        result = _execute_tool_call(
            {"agent_action": {"function": function}},
            "agent_action",
            {"action": "@goto 1 2 3"},
            game=self.game,
        )
        self.assertEqual(result["status"], "rejected")
        function.assert_not_called()

    def test_background_chat_step_never_calls_old_autonomy(self):
        from agent.multi.chat_agent import ChatAgent

        agent = ChatAgent.__new__(ChatAgent)
        agent.ctx_swarm = {"game": self.game}
        agent._autonomy = Mock()
        self.assertFalse(agent._background_step())
        agent._autonomy.tick.assert_not_called()

    def test_native_prompt_keeps_personality_and_uses_enabled_database_backstory(self):
        from agent.multi.chat_agent import ChatAgent
        from agent.tools import tools_config
        from character.character_manager import CharacterManager

        agent = ChatAgent.__new__(ChatAgent)
        agent.ctx_swarm = {"game": self.game, "states": {"personality": "saruman"}}
        rows = [
            {
                "id": "personal",
                "kind": "authored_backstory",
                "event": "lore.books",
                "text": "У окна Ортханка я однажды оставил раскрытую карту.",
                "enabled": True,
            },
            {
                "id": "disabled",
                "kind": "authored_backstory",
                "event": "lore",
                "text": "DISABLED MEMORY",
                "enabled": False,
            },
        ]
        with (
            patch.object(tools_config, "db", Mock(action_voice_records=rows)),
            patch(
                "agent.multi.chat_agent.CharacterManager.get_main_character",
                return_value=CharacterManager("Saruman", force_new=True),
            ),
        ):
            prompt = agent._build_system_prompt()
        self.assertIn(rows[0]["text"], prompt)
        self.assertNotIn("DISABLED MEMORY", prompt)
        self.assertNotIn("ensure_scepter", prompt)
        self.assertNotIn("Комментируй каждое осмысленное действие", prompt)
        self.assertIn("Не комментируй каждый шаг", prompt)
        self.assertIn("собственные намерения", prompt)

    def test_distinct_command_calls_can_repeat_intentionally_but_retries_deduplicate(
        self,
    ):
        first = submit_command(self.game, "@goto 10 64 20", "call1")
        retry = submit_command(self.game, "@goto 10 64 20", "call1")
        second = submit_command(self.game, "@goto 10 64 20", "call2")
        self.assertEqual(first["goal"]["id"], retry["goal"]["id"])
        self.assertNotEqual(first["goal"]["id"], second["goal"]["id"])

    def test_native_gradio_view_and_operator_goal_without_llm(self):
        from saruman.autonomy_panel import AutonomyPanel

        self.game["mod_runtime"] = {
            "status": "running",
            "defense": {"owner": "UNION_CLEF"},
        }
        panel = AutonomyPanel(
            lambda: (self.game, True), Path(self.temp.name) / "weights.json"
        )
        panel.submit_trees(2)
        self.game["mod_goals"] = GoalStore().list()
        self.assertEqual(self.game["mod_goals"][0]["source"], "operator")
        view = panel.view()
        self.assertIn("0/2", view[0])
        self.assertEqual([row[0] for row in view[1]], ["Auto Defense", "TreeGrow", "UnionClef"])

    def test_native_panel_has_no_legacy_activity_editor_and_saves_only_tree_switch(
        self,
    ):
        import gradio as gr

        from saruman.autonomy_panel import add_autonomy_panel

        with gr.Blocks() as app:
            panel = add_autonomy_panel(
                lambda: (self.game, True), Path(self.temp.name) / "weights.json"
            )
        labels = [getattr(block, "label", "") for block in app.blocks.values()]
        self.assertNotIn("Список самостоятельных занятий", labels)
        self.assertFalse(any(label and label.startswith("Вес:") for label in labels))
        before = panel.settings()[0]
        panel.set_native_background(False)
        after = panel.settings()[0]
        self.assertEqual(after["trees"], 0)
        self.assertEqual(
            {k: v for k, v in before.items() if k != "trees"},
            {k: v for k, v in after.items() if k != "trees"},
        )
        panel.set_native_background(True)
        self.assertGreater(panel.settings()[0]["trees"], 0)

    def test_native_layout_before_bridge_initializes_keeps_correct_headers(self):
        import gradio as gr

        from saruman.autonomy_panel import add_autonomy_panel

        startup_game = {}
        with gr.Blocks() as app:
            panel = add_autonomy_panel(
                lambda: (startup_game, False),
                Path(self.temp.name) / "startup.json",
                mode="mods",
            )
        tables = [b for b in app.blocks.values() if isinstance(b, gr.Dataframe)]
        modules = next(b for b in tables if b.label == "Подключённые модовые навыки")
        self.assertEqual(modules.headers, ["Мод", "Назначение", "Запуск", "Состояние"])
        labels = [getattr(b, "label", "") for b in app.blocks.values()]
        self.assertFalse(any(label and label.startswith("Вес:") for label in labels))
        before = panel.view()
        startup_game.update(
            automation_mode="mods",
            mod_runtime={"status": "running", "defense_enabled": True},
        )
        after = panel.view()
        self.assertEqual(len(before[1][0]), len(modules.headers))
        self.assertEqual(len(after[1][0]), len(modules.headers))
        self.assertEqual([row[0] for row in after[1]], ["Auto Defense", "TreeGrow", "UnionClef"])


if __name__ == "__main__":
    unittest.main()
