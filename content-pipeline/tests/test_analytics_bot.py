"""Tests for notifier/analytics_bot.py (Phase 6 — TikTok CSV import handlers)."""
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
from notifier import analytics_bot


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dbpath = os.path.join(self.tmp, "test.db")
        self.state = os.path.join(self.tmp, ".analytics_state.json")
        self._patches = [
            patch.object(db.config, "DB_PATH", self.dbpath),
            patch.object(analytics_bot, "_STATE_FILE", self.state),
        ]
        for p in self._patches:
            p.start()
        db.init_db()
        migrate.migrate_up()

    def tearDown(self):
        for p in self._patches:
            p.stop()


class TestFlow(Base):
    def test_start_sets_awaiting(self):
        self.assertFalse(analytics_bot.is_awaiting_csv())
        analytics_bot.start_import_tiktok_csv()
        self.assertTrue(analytics_bot.is_awaiting_csv())

    def test_handle_csv_imports_and_clears_state(self):
        analytics_bot.start_import_tiktok_csv()
        csv_text = ("Video link,Total views,Likes\n"
                    "https://www.tiktok.com/@x/video/111,100,10\n")
        reply = analytics_bot.handle_csv_document(csv_text, "stats.csv")
        self.assertIn("Đã nạp", reply)
        self.assertFalse(analytics_bot.is_awaiting_csv())
        self.assertEqual(len(vm.latest_per_video(platform="tiktok")), 1)

    def test_non_csv_filename_rejected(self):
        analytics_bot.start_import_tiktok_csv()
        reply = analytics_bot.handle_csv_document("x", "photo.png")
        self.assertIn("không phải .csv", reply)
        self.assertFalse(analytics_bot.is_awaiting_csv())

    def test_skip_clears_state(self):
        analytics_bot.start_import_tiktok_csv()
        self.assertIsNotNone(analytics_bot.skip_awaiting())
        self.assertFalse(analytics_bot.is_awaiting_csv())

    def test_skip_when_idle_returns_none(self):
        self.assertIsNone(analytics_bot.skip_awaiting())


if __name__ == "__main__":
    unittest.main()
