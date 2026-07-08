"""Tests for analytics/experiment_compare.py (Phase 6)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import storage.database as db
import storage.migrate as migrate
import storage.video_metrics as vm
from analytics import experiment_compare as ec


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dbpath = os.path.join(self.tmp, "test.db")
        self._patch = patch.object(db.config, "DB_PATH", self.dbpath)
        self._patch.start()
        db.init_db()
        migrate.migrate_up()

    def tearDown(self):
        self._patch.stop()

    def _tag(self, experiment, arm, views):
        vid = db.insert_video("short", "s", track="drama")
        db.set_video_experiment(vid, experiment, arm)
        vm.upsert_metric("youtube", f"ext{vid}", video_id=vid, views=views)
        return vid


class TestCompareArms(Base):
    def test_enough_samples_and_better_arm(self):
        for i in range(6):
            self._tag("exp1", "A", 1000 + i)
            self._tag("exp1", "B", 500 + i)
        r = ec.compare_arms("exp1", "views", min_samples=5)
        self.assertTrue(r["enough_samples"])
        self.assertFalse(r["recommended_samples_met"])  # 6 < 10
        self.assertEqual(r["better"], "A")
        self.assertGreater(r["delta"], 0)
        self.assertIsNotNone(r["p_value"])

    def test_insufficient_samples_flagged(self):
        self._tag("exp2", "A", 100)
        self._tag("exp2", "B", 200)
        r = ec.compare_arms("exp2", "views", min_samples=5)
        self.assertFalse(r["enough_samples"])
        self.assertIn("Chưa đủ mẫu", r["note"])

    def test_single_arm_not_enough_samples(self):
        for _ in range(6):
            self._tag("exp_solo", "A", 1000)  # chỉ arm A, không có arm B
        r = ec.compare_arms("exp_solo", "views", min_samples=5)
        self.assertFalse(r["enough_samples"])  # 1 arm không đủ để so
        self.assertFalse(r["recommended_samples_met"])

    def test_rate_metric_averaged_across_platforms(self):
        vid = db.insert_video("short", "s", track="drama")
        db.set_video_experiment(vid, "exp_rate", "A")
        vm.upsert_metric("youtube", "ytx", video_id=vid, retention_50_pct=55)
        vm.upsert_metric("tiktok", "ttx", video_id=vid, retention_50_pct=45)
        m = ec._metric_by_video_id("retention_50_pct")
        self.assertEqual(m[vid], 50.0)  # trung bình, KHÔNG phải 100

    def test_count_metric_summed_across_platforms(self):
        vid = db.insert_video("short", "s", track="drama")
        db.set_video_experiment(vid, "exp_sum", "A")
        vm.upsert_metric("youtube", "ytv", video_id=vid, views=1000)
        vm.upsert_metric("tiktok", "ttv", video_id=vid, views=500)
        m = ec._metric_by_video_id("views")
        self.assertEqual(m[vid], 1500)

    def test_video_without_metrics_excluded(self):
        vid = db.insert_video("short", "s", track="drama")
        db.set_video_experiment(vid, "exp3", "A")  # no metrics
        r = ec.compare_arms("exp3", "views")
        self.assertEqual(r["arms"], {})
        self.assertIn("Chưa có video", r["note"])

    def test_format_comparison_renders(self):
        for i in range(5):
            self._tag("exp4", "A", 1000)
            self._tag("exp4", "B", 900)
        text = ec.format_comparison(ec.compare_arms("exp4", "views"))
        self.assertIn("exp4", text)
        self.assertIn("Arm A", text)


if __name__ == "__main__":
    unittest.main()
