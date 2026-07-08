"""Tests for analytics/tiktok_csv.py (Phase 6 — TikTok Studio CSV parser)."""
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
from analytics import tiktok_csv as tc


class TestParse(unittest.TestCase):
    def test_parse_english_headers_and_suffixes(self):
        csv_text = (
            "Video title,Video link,Post time,Total views,Likes,Comments,Shares,"
            "Average watch time,Watched full video\n"
            "T1,https://www.tiktok.com/@x/video/7412345678901234567,2026-07-01,"
            "\"1,234\",1.2K,45,12,0:12,38%\n"
        )
        recs = tc.parse_csv_text(csv_text)
        self.assertEqual(len(recs), 1)
        r = recs[0]
        self.assertEqual(r["external_id"], "7412345678901234567")
        self.assertEqual(r["views"], 1234)
        self.assertEqual(r["likes"], 1200)      # 1.2K
        self.assertEqual(r["avg_view_duration_seconds"], 12.0)  # 0:12
        self.assertEqual(r["retention_50_pct"], 38.0)
        self.assertEqual(r["snapshot_date"], "2026-07-01")

    def test_vietnamese_headers(self):
        csv_text = ("Tiêu đề,Liên kết video,Lượt xem,Lượt thích\n"
                    "T,https://www.tiktok.com/@x/video/999888777666,500,50\n")
        r = tc.parse_csv_text(csv_text)[0]
        self.assertEqual(r["external_id"], "999888777666")
        self.assertEqual(r["views"], 500)
        self.assertEqual(r["likes"], 50)

    def test_row_without_id_or_title_skipped(self):
        csv_text = "Total views,Likes\n100,10\n"
        self.assertEqual(tc.parse_csv_text(csv_text), [])

    def test_duration_mmss(self):
        self.assertEqual(tc._duration_seconds("1:05"), 65.0)
        self.assertEqual(tc._duration_seconds("12s"), 12.0)
        self.assertIsNone(tc._duration_seconds(""))

    def test_num_suffixes(self):
        self.assertEqual(tc._num("2.5M"), 2_500_000.0)
        self.assertEqual(tc._num("1,000"), 1000.0)
        self.assertIsNone(tc._num("abc"))


class TestImport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dbpath = os.path.join(self.tmp, "test.db")
        self._patch = patch.object(db.config, "DB_PATH", self.dbpath)
        self._patch.start()
        db.init_db()
        migrate.migrate_up()

    def tearDown(self):
        self._patch.stop()

    def test_import_upserts_and_summary(self):
        csv_text = (
            "Video link,Total views,Likes\n"
            "https://www.tiktok.com/@x/video/111,100,10\n"
            "https://www.tiktok.com/@x/video/222,200,20\n"
        )
        summary = tc.import_csv_text(csv_text, snapshot_date="2026-07-05")
        self.assertEqual(summary["imported"], 2)
        self.assertEqual(summary["skipped"], 0)
        latest = vm.latest_per_video(platform="tiktok")
        self.assertEqual({r["external_id"] for r in latest}, {"111", "222"})

    def test_forced_snapshot_date_overrides(self):
        csv_text = ("Video link,Post time,Total views\n"
                    "https://www.tiktok.com/@x/video/111,2026-07-01,100\n")
        tc.import_csv_text(csv_text, snapshot_date="2026-07-09")
        self.assertEqual(vm.latest_per_video()[0]["snapshot_date"], "2026-07-09")


if __name__ == "__main__":
    unittest.main()
