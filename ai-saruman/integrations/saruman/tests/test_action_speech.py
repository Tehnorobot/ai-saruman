import random
import runpy
import unittest
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text

from saruman.action_speech import ActionSpeech, backstory_prompt, seed_records


class ActionSpeechTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.speech = ActionSpeech(rng=random.Random(1), clock=lambda: self.now)

    def test_saplings_do_not_become_claimed_grown_trees_and_unknown_results_are_silent(
        self,
    ):
        phrases = []
        for _ in range(8):
            phrases.append(self.speech.say("planted", "trees"))
            self.now += 31
        self.assertEqual(len(set(phrases)), 8)
        self.assertTrue(all("урожай" not in phrase for phrase in phrases))
        self.assertIsNone(self.speech.say("interaction_unconfirmed", "trees"))
        self.assertIsNone(self.speech.say("blocked", "trees"))

    def test_lore_is_occasional_and_never_added_in_battle_and_speech_is_rate_limited(
        self,
    ):
        self.now = 61
        self.speech.rng.random = lambda: 0.0
        fifth = self.speech.say("harvested")
        lore = [r for r in self.speech.records if r["kind"] == "authored_backstory"]
        self.assertTrue(any(row["text"] in fifth for row in lore))
        self.assertTrue(
            any(
                row["text"] in fifth and row["event"] == "lore.provisions"
                for row in lore
            )
        )
        self.assertIsNone(self.speech.say("needs_move"))
        for _ in range(5):
            self.now += 31
            battle = self.speech.say("attacked")
            self.assertFalse(any(row["text"] in battle for row in lore))

    def test_database_overrides_and_explicitly_empty_bank_are_respected(self):
        self.speech.replace(
            [
                {
                    "id": "custom",
                    "kind": "phrase",
                    "event": "planted",
                    "text": "Custom",
                    "enabled": False,
                }
            ]
        )
        self.assertIsNone(self.speech.say("planted"))
        self.speech.replace([])
        self.assertEqual(self.speech.snapshot()["phrases"], 0)

    def test_first_battle_comment_can_interrupt_peaceful_speech_without_attack_spam(
        self,
    ):
        self.speech.say("planted")
        self.now += 1
        self.assertIsNotNone(self.speech.say("attacked"))
        self.now += 1
        self.assertIsNone(self.speech.say("attacked"))

    def test_real_migration_seeds_once_preserves_edits_and_can_downgrade(self):
        migration = runpy.run_path(
            str(
                Path(__file__).resolve().parents[1]
                / "migrations/versions/b7c8d9e0f1a2_action_voice_lines.py"
            )
        )
        engine = create_engine("sqlite:///:memory:")
        self.addCleanup(engine.dispose)
        with (
            engine.begin() as connection,
            Operations.context(MigrationContext.configure(connection)),
        ):
            migration["upgrade"]()
            rows = (
                connection.execute(
                    text(
                        "select id, kind, event, text, enabled from action_voice_lines"
                    )
                )
                .mappings()
                .all()
            )
            # The historical schema migration keeps its original catalogue.
            self.assertEqual(len(rows), 86)
            self.assertGreaterEqual(
                sum(r["kind"] == "authored_backstory" for r in rows), 30
            )
            self.speech.replace(rows)
            self.assertIsNotNone(self.speech.say("planted"))
            connection.execute(
                text("update action_voice_lines set text='Edited' where id='planted.1'")
            )
            # Reads/normal startup do not reseed over an operator's edits.
            self.assertEqual(
                connection.execute(
                    text("select text from action_voice_lines where id='planted.1'")
                ).scalar_one(),
                "Edited",
            )
            migration["downgrade"]()

    def test_refresh_migration_preserves_custom_text_and_disabled_rows(self):
        root = Path(__file__).resolve().parents[1] / "migrations/versions"
        original = runpy.run_path(str(root / "b7c8d9e0f1a2_action_voice_lines.py"))
        refresh = runpy.run_path(str(root / "c9d0e1f2a3b4_saruman_personal_voice.py"))
        engine = create_engine("sqlite:///:memory:")
        self.addCleanup(engine.dispose)
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            original["upgrade"]()
            db.execute(
                text(
                    "update action_voice_lines set text='My wording' where id='needs_move.1'"
                )
            )
            db.execute(
                text(
                    "update action_voice_lines set enabled=false where id='plant_sapling.1'"
                )
            )
            refresh["upgrade"]()
            refresh["upgrade"]()  # Safe even if an offline upgrade is reapplied.
            rows = db.execute(text("select * from action_voice_lines")).mappings().all()
            by_id = {row["id"]: row for row in rows}
            self.assertEqual(by_id["needs_move.1"]["text"], "My wording")
            self.assertFalse(by_id["plant_sapling.1"]["enabled"])
            self.assertEqual(by_id["lore.1"]["event"], "lore.trees")
            self.assertEqual(
                len([row for row in rows if row["kind"] == "authored_backstory"]), 48
            )
            self.assertEqual(
                by_id["needs_move.8"]["text"],
                next(
                    row["text"] for row in seed_records() if row["id"] == "needs_move.8"
                ),
            )
            refresh["downgrade"]()
            self.assertEqual(
                db.execute(
                    text("select text from action_voice_lines where id='needs_move.1'")
                ).scalar_one(),
                "My wording",
            )

    def test_backstory_is_balanced_cached_input_and_respects_disabled_bank(self):
        prompt = backstory_prompt()
        for topic in (
            "trees",
            "travel",
            "books",
            "teaching",
            "politics",
            "craft",
            "provisions",
            "solitude",
        ):
            first = next(r for r in seed_records() if r["event"] == "lore." + topic)
            self.assertIn(first["text"], prompt)
        self.assertEqual(prompt, backstory_prompt())
        self.assertIn("not Tolkien canon", prompt)
        self.assertEqual(backstory_prompt([]), "")
        self.assertEqual(
            backstory_prompt(
                [{"kind": "authored_backstory", "enabled": False, "text": "Hidden"}]
            ),
            "",
        )

    def test_peaceful_speech_can_remain_silent_and_stories_do_not_follow_a_count(self):
        self.speech.rng.random = lambda: 1.0
        first = self.speech.say("needs_move")
        self.now = 13
        self.assertIsNone(self.speech.say("needs_move"))
        for i in range(1, 7):
            self.now = i * 31
            comment = self.speech.say("needs_move")
            self.assertIsNotNone(comment)
            self.assertFalse(
                any(
                    r["text"] in comment
                    for r in self.speech.records
                    if r["kind"] == "authored_backstory"
                )
            )
        self.assertNotIn("назначенн", first)
