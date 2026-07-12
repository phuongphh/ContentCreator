"""Tests for storage/collector_health.py (Phase 2 — Operational hardening)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import storage.database as db
import storage.migrate as migrate
import storage.collector_health as health


class HealthTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dbpath = os.path.join(self.tmp, "test.db")
        self._patch = patch.object(db.config, "DB_PATH", self.dbpath)
        self._patch.start()
        db.init_db()
        migrate.migrate_up()

    def tearDown(self):
        self._patch.stop()


class TestRecordAndGetSuccess(HealthTestBase):
    def test_never_recorded_returns_none(self):
        self.assertIsNone(health.get_last_success("reddit_drama"))

    def test_record_then_get_returns_recent_time(self):
        health.record_success("reddit_drama")
        last = health.get_last_success("reddit_drama")
        self.assertIsNotNone(last)
        age = datetime.now(timezone.utc) - last
        self.assertLess(age.total_seconds(), 10)

    def test_record_twice_updates_timestamp(self):
        # Insert an old row directly, then record_success should upsert it
        # to "now" rather than erroring on the existing primary key.
        conn = db.get_connection()
        try:
            conn.execute(
                "INSERT INTO collector_health (name, last_success) VALUES (?, ?)",
                ("reddit_drama", "2020-01-01 00:00:00"),
            )
            conn.commit()
        finally:
            conn.close()

        health.record_success("reddit_drama")
        last = health.get_last_success("reddit_drama")
        self.assertGreater(last.year, 2020)

    def test_independent_per_collector_name(self):
        health.record_success("reddit_drama")
        self.assertIsNone(health.get_last_success("other_collector"))


class TestIsStale(HealthTestBase):
    def test_never_run_is_stale(self):
        self.assertTrue(health.is_stale("reddit_drama"))

    def test_just_ran_is_not_stale(self):
        health.record_success("reddit_drama")
        self.assertFalse(health.is_stale("reddit_drama", max_age_days=2.0))

    def test_old_run_is_stale(self):
        conn = db.get_connection()
        old_ts = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
        try:
            conn.execute(
                "INSERT INTO collector_health (name, last_success) VALUES (?, ?)",
                ("reddit_drama", old_ts),
            )
            conn.commit()
        finally:
            conn.close()
        self.assertTrue(health.is_stale("reddit_drama", max_age_days=2.0))

    def test_boundary_just_under_threshold_not_stale(self):
        conn = db.get_connection()
        recent_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        try:
            conn.execute(
                "INSERT INTO collector_health (name, last_success) VALUES (?, ?)",
                ("reddit_drama", recent_ts),
            )
            conn.commit()
        finally:
            conn.close()
        self.assertFalse(health.is_stale("reddit_drama", max_age_days=2.0))


class TestCheckAndAlert(HealthTestBase):
    def test_alerts_for_stale_collectors_only(self):
        health.record_success("healthy_one")
        with patch("notifier.telegram_bot.send_alert") as alert:
            stale = health.check_and_alert(["healthy_one", "never_ran"], max_age_days=2.0)
        self.assertEqual(stale, ["never_ran"])
        alert.assert_called_once()
        self.assertIn("never_ran", alert.call_args[0][0])

    def test_no_alert_when_all_healthy(self):
        health.record_success("a")
        health.record_success("b")
        with patch("notifier.telegram_bot.send_alert") as alert:
            stale = health.check_and_alert(["a", "b"], max_age_days=2.0)
        self.assertEqual(stale, [])
        alert.assert_not_called()


class TestCheckDramaBacklog(HealthTestBase):
    def _seed(self, n, status="pending"):
        import storage.stories as stories
        for i in range(n):
            sid = stories.insert_story("vn_original", f"seed_{status}_{i}", "body")
            if status != "pending":
                stories.update_status(sid, status)

    def test_alerts_when_backlog_below_threshold(self):
        self._seed(1)  # only 1 producible, threshold 3
        with patch("config.DRAMA_BACKLOG_MIN", 3), \
             patch("notifier.telegram_bot.send_alert") as alert:
            alerted = health.check_drama_backlog()
        self.assertTrue(alerted)
        alert.assert_called_once()
        self.assertIn("Drama backlog", alert.call_args[0][0])

    def test_no_alert_when_backlog_ok(self):
        self._seed(3)
        with patch("config.DRAMA_BACKLOG_MIN", 3), \
             patch("notifier.telegram_bot.send_alert") as alert:
            alerted = health.check_drama_backlog()
        self.assertFalse(alerted)
        alert.assert_not_called()

    def test_explicit_min_count_overrides_config(self):
        self._seed(2)
        with patch("notifier.telegram_bot.send_alert") as alert:
            self.assertFalse(health.check_drama_backlog(min_count=2))  # 2 >= 2, ok
            self.assertTrue(health.check_drama_backlog(min_count=3))   # 2 < 3, alert
        alert.assert_called_once()


if __name__ == "__main__":
    unittest.main()
