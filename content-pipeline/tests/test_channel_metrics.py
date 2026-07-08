"""Tests for storage/channel_metrics.py (Phase 6)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import storage.database as db
import storage.migrate as migrate
import storage.channel_metrics as cm


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


class TestChannelMetrics(Base):
    def test_upsert_idempotent_same_day(self):
        cm.upsert_channel_metric("ai_youtube", "youtube",
                                 subscribers=1000, snapshot_date="2026-07-01")
        cm.upsert_channel_metric("ai_youtube", "youtube",
                                 subscribers=1100, snapshot_date="2026-07-01")
        rng = cm.get_range("ai_youtube")
        self.assertEqual(len(rng), 1)
        self.assertEqual(rng[-1]["subscribers"], 1100)

    def test_subs_gained_sums_over_range(self):
        cm.upsert_channel_metric("ai_youtube", "youtube",
                                 subscribers_gained=10, snapshot_date="2026-07-01")
        cm.upsert_channel_metric("ai_youtube", "youtube",
                                 subscribers_gained=15, snapshot_date="2026-07-02")
        self.assertEqual(cm.subs_gained("ai_youtube", "2026-07-01"), 25)
        self.assertEqual(cm.subs_gained("ai_youtube", "2026-07-02"), 15)

    def test_unknown_field_rejected(self):
        with self.assertRaises(ValueError):
            cm.upsert_channel_metric("ai_youtube", "youtube", bogus=1)


if __name__ == "__main__":
    unittest.main()
