"""Tests for storage/video_metrics.py (Phase 6)."""
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
import storage.scheduled_posts as sp


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


class TestUpsert(Base):
    def test_upsert_idempotent_same_day(self):
        vm.upsert_metric("youtube", "abc", video_id=1, views=100, snapshot_date="2026-07-01")
        vm.upsert_metric("youtube", "abc", views=150, snapshot_date="2026-07-01")
        latest = vm.latest_per_video()
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0]["views"], 150)

    def test_different_days_separate_rows(self):
        vm.upsert_metric("youtube", "abc", video_id=7, views=100, snapshot_date="2026-07-01")
        vm.upsert_metric("youtube", "abc", video_id=7, views=200, snapshot_date="2026-07-02")
        # Hai snapshot khác ngày = 2 dòng, nhưng latest_per_video chỉ trả dòng mới nhất.
        self.assertEqual(len(vm.get_metrics_for_video(7)), 2)
        latest = vm.latest_per_video()
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0]["views"], 200)  # newest wins

    def test_partial_update_keeps_other_columns(self):
        vm.upsert_metric("youtube", "abc", views=100, likes=10, snapshot_date="2026-07-01")
        vm.upsert_metric("youtube", "abc", views=150, snapshot_date="2026-07-01")  # no likes
        latest = vm.latest_per_video()[0]
        self.assertEqual(latest["views"], 150)
        self.assertEqual(latest["likes"], 10)  # preserved

    def test_unknown_metric_rejected(self):
        with self.assertRaises(ValueError):
            vm.upsert_metric("youtube", "abc", bogus=1)


class TestResolveVideoId(Base):
    def test_resolve_via_scheduled_posts(self):
        vid = db.insert_video("short", "x")
        post_id = sp.insert_post(vid, "drama_youtube", "2099-01-01 12:00:00")
        sp.record_platform_id(post_id, "yt_external_1", url="https://youtu.be/yt_external_1")
        self.assertEqual(vm.resolve_video_id("youtube", "yt_external_1"), vid)

    def test_upsert_auto_resolves_video_id(self):
        vid = db.insert_video("short", "x")
        post_id = sp.insert_post(vid, "drama_youtube", "2099-01-01 12:00:00")
        sp.record_platform_id(post_id, "yt_ext_2")
        vm.upsert_metric("youtube", "yt_ext_2", views=5)  # no video_id passed
        self.assertEqual(vm.latest_per_video()[0]["video_id"], vid)

    def test_unresolvable_leaves_null(self):
        vm.upsert_metric("tiktok", "no_map", views=5)
        self.assertIsNone(vm.latest_per_video()[0]["video_id"])


class TestTopVideos(Base):
    def test_top_and_bottom(self):
        vm.upsert_metric("youtube", "a", views=100, retention_50_pct=50)
        vm.upsert_metric("youtube", "b", views=300, retention_50_pct=20)
        vm.upsert_metric("youtube", "c", views=200, retention_50_pct=80)
        top = vm.top_videos("views", limit=2)
        self.assertEqual([r["external_id"] for r in top], ["b", "c"])
        bottom = vm.top_videos("retention_50_pct", limit=1, ascending=True)
        self.assertEqual(bottom[0]["external_id"], "b")

    def test_null_metric_excluded(self):
        vm.upsert_metric("youtube", "a", views=100)  # no retention
        vm.upsert_metric("youtube", "b", views=50, retention_50_pct=30)
        top = vm.top_videos("retention_50_pct")
        self.assertEqual([r["external_id"] for r in top], ["b"])


if __name__ == "__main__":
    unittest.main()
