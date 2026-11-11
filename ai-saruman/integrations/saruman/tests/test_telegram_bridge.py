"""Telegram has its own selected profile and no game-tool access."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from saruman.telegram_bridge import make_director
from _saruman_telegram.automation import Store


class BridgeTests(unittest.TestCase):
    def test_post_generation_uses_separate_telegram_role(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "events.sqlite3")
            role_store = Mock()
            with patch("saruman.role_client.RoleClient") as factory:
                client = factory.return_value
                client.complete.return_value = {"text": "Дерево выросло.", "event_ids": ["one"]}
                director = make_director(role_store, "game-session", "Character personality", store)
                result = director.draft({"events": [{"id": "one"}]})
                factory.assert_called_once_with(role_store, "telegram", "game-session:telegram")
                client.resume.assert_called_once()
                args = client.complete.call_args.args
                self.assertIn("Character personality", args[0])
                self.assertEqual(args[2]["required"], ["text", "event_ids"])
                self.assertEqual(result["event_ids"], ["one"])
