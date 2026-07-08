"""Tests for analytics/stats.py (Phase 6 — Welch t-test không cần scipy)."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from analytics import stats


class TestWelchTTest(unittest.TestCase):
    def test_identical_samples_p_one(self):
        r = stats.welch_ttest([10, 20, 30], [10, 20, 30])
        self.assertEqual(r["t"], 0.0)
        self.assertAlmostEqual(r["p_value"], 1.0, places=6)

    def test_known_value_matches_scipy(self):
        # scipy.stats.ttest_ind([1,2,3],[4,5,6], equal_var=False) → t=-3.674, p≈0.02131
        r = stats.welch_ttest([1, 2, 3], [4, 5, 6])
        self.assertAlmostEqual(r["t"], -3.6742, places=3)
        self.assertAlmostEqual(r["df"], 4.0, places=6)
        self.assertAlmostEqual(r["p_value"], 0.02131, places=4)

    def test_p_value_symmetric_in_argument_order(self):
        r1 = stats.welch_ttest([1, 2, 3], [4, 5, 6])
        r2 = stats.welch_ttest([4, 5, 6], [1, 2, 3])
        self.assertAlmostEqual(r1["p_value"], r2["p_value"], places=9)

    def test_too_few_samples_returns_none(self):
        r = stats.welch_ttest([1], [2, 3])
        self.assertIsNone(r["t"])
        self.assertIsNone(r["p_value"])
        self.assertEqual(r["n_a"], 1)

    def test_zero_variance_both_groups(self):
        r = stats.welch_ttest([5, 5, 5], [5, 5, 5])
        self.assertIsNone(r["t"])  # denom == 0 → t vô định


if __name__ == "__main__":
    unittest.main()
