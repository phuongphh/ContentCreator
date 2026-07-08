"""Tests for storage/quota.py (Phase 5 — YouTube quota tracking)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import storage.database as db
import storage.migrate as migrate
import storage.quota as quota


class QuotaBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dbpath = os.path.join(self.tmp, "test.db")
        self._patches = [
            patch.object(db.config, "DB_PATH", self.dbpath),
            patch.object(quota.config, "YOUTUBE_DAILY_QUOTA", 1000),
            patch.object(quota.config, "QUOTA_ALERT_RATIO", 0.8),
        ]
        for p in self._patches:
            p.start()
        db.init_db()
        migrate.migrate_up()

    def tearDown(self):
        for p in self._patches:
            p.stop()


class TestQuotaDate(unittest.TestCase):
    def test_pacific_date_rolls_back_from_utc(self):
        # 05:00 UTC = 21:00/22:00 hôm TRƯỚC theo giờ Pacific
        now = datetime(2026, 7, 7, 5, 0, tzinfo=timezone.utc)
        self.assertEqual(quota.quota_date(now), "2026-07-06")

    def test_pacific_same_day_in_utc_evening(self):
        now = datetime(2026, 7, 7, 20, 0, tzinfo=timezone.utc)
        self.assertEqual(quota.quota_date(now), "2026-07-07")


class TestAddUnits(QuotaBase):
    def test_accumulates(self):
        quota.add_units(100)
        total, _ = quota.add_units(200)
        self.assertEqual(total, 300)
        self.assertEqual(quota.units_used_today(), 300)

    def test_crossed_flag_fires_once(self):
        _, crossed = quota.add_units(700)
        self.assertFalse(crossed)          # 700 < 800
        _, crossed = quota.add_units(200)
        self.assertTrue(crossed)           # 700 → 900 băng qua 800
        _, crossed = quota.add_units(50)
        self.assertFalse(crossed)          # đã ở trên ngưỡng — không alert lại

    def test_record_youtube_units_alerts_on_cross(self):
        with patch("notifier.telegram_bot.send_alert") as alert:
            quota.record_youtube_units(700)
            alert.assert_not_called()
            quota.record_youtube_units(200)
            alert.assert_called_once()

    def test_alert_failure_does_not_break_recording(self):
        with patch("notifier.telegram_bot.send_alert", side_effect=RuntimeError("down")):
            total = quota.record_youtube_units(900)
        self.assertEqual(total, 900)


if __name__ == "__main__":
    unittest.main()
