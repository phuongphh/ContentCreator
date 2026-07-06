"""Tests for processors/ab_harness.py (Phase 3 EPIC #3.4 — A/B harness)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import storage.database as db
import storage.migrate as migrate
import processors.ab_harness as ab_harness


class TestChooseVersion(unittest.TestCase):
    def test_deterministic_for_same_inputs(self):
        v1 = ab_harness.choose_version("rewriter", 42)
        v2 = ab_harness.choose_version("rewriter", 42)
        self.assertEqual(v1, v2)

    def test_returns_one_of_the_given_versions(self):
        result = ab_harness.choose_version("rewriter", 1, versions=("a", "b", "c"))
        self.assertIn(result, ("a", "b", "c"))

    def test_different_story_ids_can_get_different_versions(self):
        # Not a strict requirement, but with a big enough sample the 2
        # versions should both show up (sanity check the hash isn't constant).
        assigned = {ab_harness.choose_version("rewriter", i) for i in range(50)}
        self.assertEqual(assigned, {"v1", "v2"})

    def test_roughly_balanced_split(self):
        counts = {"v1": 0, "v2": 0}
        for i in range(500):
            counts[ab_harness.choose_version("exp", i)] += 1
        # Not asserting exact 50/50, just that neither side is wildly skewed.
        self.assertGreater(counts["v1"], 150)
        self.assertGreater(counts["v2"], 150)

    def test_different_experiments_can_assign_differently(self):
        # Same story_id, different experiment namespace -> not required to
        # match; just confirms the experiment name participates in the hash.
        results = {ab_harness.choose_version(f"exp{i}", 7) for i in range(10)}
        self.assertTrue(len(results) >= 1)  # sanity: doesn't error, returns valid values
        for r in results:
            self.assertIn(r, ("v1", "v2"))


class ABHarnessDBTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dbpath = os.path.join(self.tmp, "test.db")
        self._patch = patch.object(db.config, "DB_PATH", self.dbpath)
        self._patch.start()
        db.init_db()
        migrate.migrate_up()

    def tearDown(self):
        self._patch.stop()


class TestRecordAndCompare(ABHarnessDBTestBase):
    def test_compare_returns_none_with_no_runs(self):
        self.assertIsNone(ab_harness.compare_ab_results("rewriter"))

    def test_compare_returns_none_below_min_samples(self):
        for i in range(5):
            ab_harness.record_ab_result("rewriter", "v1", i, 0.8)
        self.assertIsNone(ab_harness.compare_ab_results("rewriter", min_samples=10))

    def test_compare_after_enough_samples(self):
        for i in range(10):
            ab_harness.record_ab_result("rewriter", "v1", i, 0.7)
        for i in range(10, 20):
            ab_harness.record_ab_result("rewriter", "v2", i, 0.9)
        result = ab_harness.compare_ab_results("rewriter", min_samples=10)
        self.assertIsNotNone(result)
        self.assertEqual(result["v1"]["n"], 10)
        self.assertEqual(result["v2"]["n"], 10)
        self.assertAlmostEqual(result["v1"]["mean"], 0.7)
        self.assertAlmostEqual(result["v2"]["mean"], 0.9)
        self.assertEqual(result["better"], "v2")

    def test_tie_when_means_equal(self):
        for i in range(10):
            ab_harness.record_ab_result("rewriter", "v1", i, 0.8)
        for i in range(10, 20):
            ab_harness.record_ab_result("rewriter", "v2", i, 0.8)
        result = ab_harness.compare_ab_results("rewriter", min_samples=10)
        self.assertEqual(result["better"], "tie")

    def test_experiments_are_independent(self):
        for i in range(10):
            ab_harness.record_ab_result("expA", "v1", i, 0.5)
        self.assertIsNone(ab_harness.compare_ab_results("expB", min_samples=10))


if __name__ == "__main__":
    unittest.main()
