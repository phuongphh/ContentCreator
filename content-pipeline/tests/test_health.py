"""Tests for webui/health.py (Phase 5 — health endpoint payload)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import storage.database as db
import storage.migrate as migrate
import webui.health as health


class HealthBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dbpath = os.path.join(self.tmp, "test.db")
        self._patch = patch.object(db.config, "DB_PATH", self.dbpath)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()


class TestPayloadMigratedDb(HealthBase):
    def setUp(self):
        super().setUp()
        db.init_db()
        migrate.migrate_up()

    def test_all_sections_present(self):
        payload = health.build_health_payload()
        for section in ("videos", "stories", "scheduler", "quota", "collectors"):
            self.assertIn(section, payload)
            self.assertNotIn("error", payload[section],
                             f"section {section} unexpectedly errored")

    def test_counts_reflect_data(self):
        vid = db.insert_video(video_type="short", script_text="x")
        db.update_video_status(vid, "pending_approval")
        import storage.scheduled_posts as sp
        sp.insert_post(vid, "drama_youtube", "2099-01-01 12:00:00")

        payload = health.build_health_payload()
        self.assertEqual(payload["videos"].get("pending_approval"), 1)
        self.assertEqual(payload["scheduler"]["by_status"], {"queued": 1})
        self.assertEqual(payload["scheduler"]["next_scheduled_at"],
                         "2099-01-01 12:00:00")
        self.assertEqual(payload["quota"]["youtube_units_used"], 0)


class TestPayloadUnmigratedDb(HealthBase):
    def test_missing_tables_error_per_section_not_whole_payload(self):
        db.init_db()  # KHÔNG chạy migrate — stories/scheduled_posts/quota thiếu
        payload = health.build_health_payload()
        self.assertNotIn("error", payload["videos"])  # bảng videos luôn có
        self.assertIn("error", payload["stories"])
        self.assertIn("error", payload["scheduler"])


if __name__ == "__main__":
    unittest.main()
