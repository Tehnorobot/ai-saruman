"""Reset actual isolated stores, preserving character data and token accounting."""

import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from data_flow.db.models.action_voice_line import ActionVoiceLine
from data_flow.db.models.base import Base
from data_flow.db.models.mind_variable import MindVariable
from data_flow.db.models.user import User
from data_flow.db.models.user_dialog import UserDialog
from data_flow.db.models.user_nickname import UserNickname
from saruman.control import submit_operator_input
from saruman.memory_reset import (
    MemoryResetPanel,
    add_memory_reset_panel,
    apply_pending_reset,
    cancel_reset,
    request_reset,
    reset_pending,
)
from saruman.mod_goals import GoalStore
from saruman.role_settings import RoleStore


class RedisFixture:
    def __init__(self):
        self.data = {
            "saruman:dict:game": {"tree_task": "old planted site"},
            "saruman:ctx_chat": {"old": "conversation"},
            "saruman:list:tts_queue": ["old speech"],
            "another_app:settings": {"keep": True},
        }

    def ping(self):
        return True

    def hget(self, name, field):
        return self.data.get(name, {}).get(field)

    def scan_iter(self, *, match, count):
        prefix = match.removesuffix("*")
        return iter([key for key in self.data if key.startswith(prefix)])

    def delete(self, *keys):
        for key in keys:
            self.data.pop(key, None)
        return len(keys)


class ResetTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.marker = self.root / "reset.json"
        self.roles_path = self.root / "roles.sqlite"
        self.goals_path = self.root / "goals.sqlite"
        env = patch.dict(
            os.environ,
            {
                "SARUMAN_ROLE_DB": str(self.roles_path),
                "SARUMAN_GOALS_DB": str(self.goals_path),
            },
        )
        env.start()
        self.addCleanup(env.stop)
        self.roles = RoleStore()
        profile = self.roles.profiles()["persona"]
        self.roles.save(
            "persona",
            {k: v for k, v in profile.items() if k != "revision"},
            paused=True,
        )
        call = self.roles.begin("persona", "old-session", profile, 10)
        self.roles.finish(call, {"total_tokens": 8})
        self.roles.save_intent(
            {"id": "old-intent", "state": "waiting", "text": "Follow Alex"}
        )
        self.goals = GoalStore()
        self.goals.submit("trees", source="operator")
        self.goals.set_meta("last_tree_site", [10, 64, 20])
        self.client = RedisFixture()
        self.engine = create_engine("sqlite://")
        self.addCleanup(self.engine.dispose)

        @event.listens_for(self.engine, "connect")
        def foreign_keys(connection, _record):
            connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)
        now = datetime.now()
        with Session(self.engine) as session:
            user = User(
                name="Alex",
                firstreg=now,
                last_interact=now,
                rank=1,
                summary="A familiar player",
            )
            session.add(user)
            session.flush()
            nick = UserNickname(
                user_id=user.user_id, nick="Alex", env="minecraft", added_date=now
            )
            session.add(nick)
            session.flush()
            dialog = UserDialog(
                user_id=user.user_id,
                bind_to_nick_id=nick.nick_id,
                content="Remember this tree",
                role="user",
                date=now,
                env="minecraft",
            )
            session.add(dialog)
            session.flush()
            session.add(
                UserDialog(
                    user_id=user.user_id,
                    bind_to_diag_id=dialog.diag_id,
                    content="I shall",
                    role="assistant",
                    date=now,
                )
            )
            session.add(MindVariable(mind_id=1, mind_name="default_mind", mood=-7))
            session.add(
                ActionVoiceLine(
                    id="authored",
                    kind="backstory",
                    event="trees",
                    text="My authored history",
                    enabled=False,
                )
            )
            session.commit()

    def apply(self):
        return apply_pending_reset(
            path=self.marker, engine=self.engine, redis_client=self.client
        )

    def test_clean_start_erases_learned_data_and_keeps_profile_bank_and_accounting(
        self,
    ):
        profiles, history = self.roles.profiles(), self.roles.history()
        request_reset(self.marker)
        result = self.apply()
        self.assertEqual(result["users"], 1)
        self.assertEqual(result["users_dialogs"], 2)
        self.assertFalse(reset_pending(self.marker))
        with Session(self.engine) as session:
            for model in (User, UserNickname, UserDialog):
                self.assertEqual(list(session.scalars(select(model))), [])
            self.assertEqual(session.get(MindVariable, 1).mood, 0)
            authored = session.get(ActionVoiceLine, "authored")
            self.assertEqual(authored.text, "My authored history")
            self.assertFalse(authored.enabled)
        self.assertEqual(self.roles.profiles(), profiles)
        self.assertEqual(self.roles.history(), history)
        self.assertEqual(self.roles.intents(), [])
        self.assertEqual(self.goals.list(), [])
        self.assertIsNone(self.goals.meta("last_tree_site"))
        self.assertEqual(self.client.data, {"another_app:settings": {"keep": True}})
        from saruman.tree_memory import capture_tree_memory

        self.assertIsNone(capture_tree_memory(self.client, "saruman:dict:game"))
        self.assertIsNone(self.apply())

    def test_scheduling_and_cancelling_do_not_change_live_memories(self):
        request_reset(self.marker)
        original = self.marker.read_bytes()
        request_reset(self.marker)
        self.assertEqual(self.marker.read_bytes(), original)
        cancel_reset(self.marker)
        self.assertFalse(reset_pending(self.marker))
        self.assertEqual(len(self.roles.intents()), 1)
        self.assertEqual(len(self.goals.list()), 1)
        self.assertIsNone(self.apply())

    def test_failed_partial_cleanup_stays_pending_and_retry_completes(self):
        request_reset(self.marker)
        with (
            patch.object(self.client, "delete", side_effect=ConnectionError("offline")),
            self.assertRaises(ConnectionError),
        ):
            self.apply()
        self.assertTrue(reset_pending(self.marker))
        with self.assertRaises(ValueError):
            cancel_reset(self.marker)
        self.apply()
        self.assertFalse(reset_pending(self.marker))
        self.assertEqual(self.roles.intents(), [])

    def test_running_other_runtime_blocks_before_any_erasure(self):
        request_reset(self.marker)
        self.client.data["saruman:dict:env"] = {
            "runtime_pid": json.dumps(os.getpid() + 100)
        }
        with (
            patch("saruman.role_settings.pid_alive", return_value=True),
            self.assertRaises(RuntimeError),
        ):
            self.apply()
        self.assertEqual(len(self.roles.intents()), 1)
        self.assertEqual(len(self.goals.list()), 1)
        self.assertEqual(json.loads(self.marker.read_text())["phase"], "pending")

    def test_running_request_of_older_runtime_also_blocks_reset(self):
        self.roles.begin("action", "other-session", self.roles.profiles()["action"], 10)
        with sqlite3.connect(self.roles_path) as db:
            db.execute(
                "UPDATE sessions SET pid=? WHERE id='other-session'",
                (os.getpid() + 100,),
            )
        request_reset(self.marker)
        with (
            patch("saruman.role_settings.pid_alive", return_value=True),
            self.assertRaises(RuntimeError),
        ):
            self.apply()
        self.assertEqual(len(self.goals.list()), 1)

    def test_relational_failure_rolls_back_before_other_stores_are_erased(self):
        def reject_delete(
            _connection, _cursor, statement, _parameters, _context, _many
        ):
            if statement.startswith("DELETE FROM users_nicknames"):
                raise RuntimeError("test rollback")

        event.listen(self.engine, "before_cursor_execute", reject_delete)
        self.addCleanup(
            event.remove, self.engine, "before_cursor_execute", reject_delete
        )
        request_reset(self.marker)
        with self.assertRaises(RuntimeError):
            self.apply()
        with Session(self.engine) as session:
            self.assertEqual(len(list(session.scalars(select(UserDialog)))), 2)
            self.assertIsNotNone(session.get(User, 1))
        self.assertEqual(len(self.roles.intents()), 1)
        self.assertIn("saruman:dict:game", self.client.data)
        self.assertTrue(reset_pending(self.marker))

    def test_missing_local_stores_are_not_created_by_cleanup(self):
        self.roles_path.unlink()
        self.goals_path.unlink()
        request_reset(self.marker)
        self.apply()
        self.assertFalse(self.roles_path.exists())
        self.assertFalse(self.goals_path.exists())

    def test_pending_reset_runs_before_checkpoint_restore_and_collectors(self):
        import ast

        source = (Path(__file__).resolve().parents[1] / "src/main.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        main = next(
            n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"
        )
        calls = {}
        for n in ast.walk(main):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
                calls.setdefault(n.func.id, n.lineno)
        self.assertLess(calls["apply_pending_reset"], calls["RedisCtxSwarm"])
        self.assertLess(calls["apply_pending_reset"], calls["Process"])
        self.assertLess(calls["apply_pending_reset"], calls["StreamerDatabase"])

    def test_panel_blocks_enabled_and_native_busy_and_builds_real_controls(self):
        state = [{}, True]
        panel = MemoryResetPanel(lambda: state, self.marker)
        self.assertIn("Stop", panel.schedule())
        state[:] = [{"mod_runtime": {"native_busy": True}}, False]
        self.assertIn("остановки", panel.schedule())
        self.assertFalse(self.marker.exists())
        state[:] = [{}, False]
        self.assertIn("Ctrl+C", panel.schedule())
        self.assertTrue(self.marker.exists())
        panel.cancel()
        import gradio as gr

        with patch("saruman.memory_reset.RESET_FILE", self.marker), gr.Blocks() as app:
            add_memory_reset_panel(lambda: state)
        self.assertTrue(
            any(
                "Сбросить игровую память" in str(getattr(b, "value", ""))
                for b in app.blocks.values()
            )
        )

    def test_reset_command_is_local_and_never_sent_to_agent_tools(self):
        panel = MemoryResetPanel(lambda: ({}, False), self.marker)
        handler, queue = Mock(), Mock()
        submit_operator_input("!reset_game_memory", handler, queue, memory_reset=panel)
        self.assertTrue(self.marker.exists())
        submit_operator_input(
            "!cancel_memory_reset", handler, queue, memory_reset=panel
        )
        self.assertFalse(self.marker.exists())
        handler.add_message.assert_not_called()
        queue.put.assert_not_called()

    def test_cli_schedules_without_service_check_or_model_call(self):
        from saruman.launcher import main

        with (
            patch("saruman.launcher.load_environment"),
            patch("saruman.memory_reset.RESET_FILE", self.marker),
            patch("saruman.doctor.check") as doctor,
        ):
            self.assertEqual(main(["reset-memory"]), 0)
            self.assertTrue(self.marker.exists())
            self.assertEqual(main(["reset-memory", "--cancel"]), 0)
        doctor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
