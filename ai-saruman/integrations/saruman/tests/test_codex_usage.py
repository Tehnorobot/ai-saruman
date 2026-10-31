"""Accounting and UI contracts without model generations or account access."""

import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from saruman import usage


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.tracker = usage.UsageTracker()

    def test_cumulative_updates_live_and_finished_are_counted_once(self):
        identifier = self.tracker.begin("gpt-6-sol", "low")
        self.tracker.update(
            identifier,
            {
                "usage": {
                    "inputTokens": 100,
                    "cachedInputTokens": 80,
                    "outputTokens": 10,
                    "reasoningOutputTokens": 5,
                    "totalTokens": 110,
                }
            },
        )
        self.tracker.update(
            identifier,
            {
                "usage": {
                    "inputTokens": 100,
                    "cachedInputTokens": 80,
                    "outputTokens": 20,
                    "reasoningOutputTokens": 10,
                    "totalTokens": 120,
                }
            },
        )
        self.assertEqual(self.tracker.snapshot()["totals"]["totalTokens"], 120)
        self.tracker.finish(identifier)
        state = self.tracker.snapshot()
        self.assertEqual(state["totals"]["totalTokens"], 120)
        self.assertEqual(
            state["totals"]["inputTokens"] + state["totals"]["outputTokens"], 120
        )
        self.assertFalse(any(state["missing"].values()))

    def test_missing_failed_usage_is_unknown_not_free(self):
        identifier = self.tracker.begin("fixture", None)
        self.tracker.finish(identifier, failed=True)
        with patch.object(usage, "usage_tracker", self.tracker):
            text, rows = usage.usage_view()
        self.assertIn("есть запросы без данных", text)
        self.assertIn("денежная стоимость запросов недоступна", text)
        self.assertEqual(rows[0][0], "ошибка")
        self.assertEqual(rows[0][3:8], ["—"] * 5)

    def test_sparse_limit_notification_preserves_other_windows_and_buckets(self):
        self.tracker.update_limits(
            {
                "rateLimitsByLimitId": {
                    "codex": {
                        "planType": "plus",
                        "primary": {
                            "usedPercent": 10,
                            "windowDurationMins": 300,
                            "resetsAt": 2000000000,
                        },
                        "secondary": {"usedPercent": 1},
                    },
                    "other": {"primary": {"usedPercent": 2}},
                }
            }
        )
        self.tracker.update_limits(
            {"rateLimits": {"limitId": "codex", "primary": {"usedPercent": 22}}}
        )
        self.tracker.limits_unavailable()
        state = self.tracker.snapshot()
        self.assertEqual(state["limits"]["codex"]["secondary"]["usedPercent"], 1)
        self.assertEqual(state["limits"]["codex"]["primary"]["windowDurationMins"], 300)
        self.assertIn("other", state["limits"])
        with patch.object(usage, "usage_tracker", self.tracker):
            text, _ = usage.usage_view()
        self.assertIn("осталось 78%", text)
        self.assertIn("могут быть устаревшими", text)

    def test_concurrent_requests_and_bounded_history_keep_lifetime_totals(self):
        def request(_):
            identifier = self.tracker.begin("fixture", "low")
            self.tracker.update(identifier, {"usage": {"totalTokens": 5}})
            self.tracker.update(identifier, {"usage": {"totalTokens": 10}})
            self.tracker.finish(identifier)

        with ThreadPoolExecutor(max_workers=4) as workers:
            list(workers.map(request, range(100)))
        state = self.tracker.snapshot()
        self.assertEqual(state["totals"]["totalTokens"], 1000)
        self.assertEqual(state["finished"], 100)
        self.assertEqual(len(state["recent"]), 20)
        self.assertFalse(state["active"])


if __name__ == "__main__":
    unittest.main()
