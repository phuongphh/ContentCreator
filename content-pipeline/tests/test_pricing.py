"""Tests for analytics/pricing.py (Phase 6 — token→USD overlay)."""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from analytics import pricing


class TestRates(unittest.TestCase):
    def test_known_model(self):
        self.assertEqual(pricing.rates_for("claude-haiku-4-5"), (1.0, 5.0))

    def test_prefix_match_dated_id(self):
        self.assertEqual(pricing.rates_for("claude-haiku-4-5-20251001"), (1.0, 5.0))

    def test_unknown_model_none(self):
        self.assertIsNone(pricing.rates_for("gpt-9"))

    def test_env_override(self):
        with patch.dict(os.environ, {"PRICE_CLAUDE_HAIKU_4_5_IN": "2",
                                     "PRICE_CLAUDE_HAIKU_4_5_OUT": "8"}):
            self.assertEqual(pricing.rates_for("claude-haiku-4-5"), (2.0, 8.0))

    def test_cost_usd(self):
        # 1M input @ $1 + 100k output @ $5 = 1.0 + 0.5 = 1.5
        self.assertAlmostEqual(pricing.cost_usd("claude-haiku-4-5", 1_000_000, 100_000), 1.5)

    def test_cost_unknown_model_none(self):
        self.assertIsNone(pricing.cost_usd("mystery", 100, 100))


class TestSummarize(unittest.TestCase):
    def test_summary_totals_and_unpriced(self):
        rows = [
            {"model": "claude-haiku-4-5", "service": "anthropic",
             "input_tokens": 1_000_000, "output_tokens": 0},
            {"model": "mystery", "service": "anthropic",
             "input_tokens": 100, "output_tokens": 50},
        ]
        s = pricing.summarize_costs(rows)
        self.assertAlmostEqual(s["total_usd"], 1.0)
        self.assertIn("mystery", s["unpriced_models"])
        self.assertEqual(s["by_model"]["claude-haiku-4-5"]["calls"], 1)


if __name__ == "__main__":
    unittest.main()
