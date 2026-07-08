"""Tests for analytics/weekly_retro.py + dashboard/data.py (Phase 6)."""
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
import storage.channel_metrics as cm
import storage.cost_logs as cl
from analytics import weekly_retro as wr
from dashboard import data as dd


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dbpath = os.path.join(self.tmp, "test.db")
        self._patch = patch.object(db.config, "DB_PATH", self.dbpath)
        self._patch.start()
        db.init_db()
        migrate.migrate_up()
        self._seed()

    def tearDown(self):
        self._patch.stop()

    def _seed(self):
        v1 = db.insert_video("short", "a", youtube_title="Vid A", track="drama")
        v2 = db.insert_video("short", "b", youtube_title="Vid B", track="drama")
        vm.upsert_metric("youtube", "e1", video_id=v1, views=5000,
                         retention_50_pct=55, snapshot_date="2026-07-06")
        vm.upsert_metric("youtube", "e2", video_id=v2, views=800,
                         retention_50_pct=30, snapshot_date="2026-07-06")
        cm.upsert_channel_metric("drama_youtube", "youtube",
                                 subscribers_gained=40, snapshot_date="2026-07-06")
        cl.record_cost("anthropic", "claude-haiku-4-5", "scorer",
                       input_tokens=1_000_000, output_tokens=0, date="2026-07-06")


class TestRetro(Base):
    def test_report_has_all_sections_and_fits_telegram(self):
        report = wr.generate_retro_report(since="2026-07-01")
        for marker in ("RETRO TUẦN", "TOP 3", "SUB GROWTH", "CHI PHÍ", "ĐỀ XUẤT"):
            self.assertIn(marker, report)
        self.assertLessEqual(len(report), wr.MAX_REPORT_CHARS)

    def test_cost_reflected(self):
        report = wr.generate_retro_report(since="2026-07-01")
        # 1M input @ $1/1M = $1.00
        self.assertIn("$1.00", report)

    def test_top_video_labelled_by_title(self):
        report = wr.generate_retro_report(since="2026-07-01")
        self.assertIn("Vid A", report)

    def test_send_uses_telegram(self):
        with patch("notifier.telegram_bot.send_alert", return_value=True) as m:
            self.assertTrue(wr.send_weekly_retro(since="2026-07-01"))
            m.assert_called_once()


class TestDashboardData(Base):
    def test_overview_uses_latest_snapshot(self):
        ov = dd.overview(since="2026-07-01")
        self.assertEqual(ov["n_videos"], 2)
        self.assertEqual(ov["views"], 5800)

    def test_format_breakdown_groups_by_track_type(self):
        rows = dd.format_breakdown(since="2026-07-01")
        self.assertTrue(any(r["track"] == "drama" for r in rows))

    def test_cost_breakdown(self):
        cb = dd.cost_breakdown(since="2026-07-01")
        self.assertAlmostEqual(cb["summary"]["total_usd"], 1.0)


if __name__ == "__main__":
    unittest.main()
