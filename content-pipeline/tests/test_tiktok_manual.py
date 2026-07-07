"""Tests for publisher/tiktok_manual.py (Phase 5 — manual export queue)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import storage.database as db
import publisher.tiktok_manual as tm


class TikTokManualBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dbpath = os.path.join(self.tmp, "test.db")
        self.queue = os.path.join(self.tmp, "queue")
        self._patch = patch.object(db.config, "DB_PATH", self.dbpath)
        self._patch.start()
        db.init_db()

    def tearDown(self):
        self._patch.stop()

    def _make_video_with_file(self, caption="Chuyện mẹ chồng", hashtags="#drama #mechong"):
        video_id = db.insert_video(video_type="short", script_text="x",
                                   tiktok_caption=caption, tiktok_hashtags=hashtags)
        src = os.path.join(self.tmp, f"src_{video_id}.mp4")
        with open(src, "wb") as f:
            f.write(b"fake mp4 bytes")
        db.update_video_paths(video_id, video_path=src)
        return video_id


class TestExport(TikTokManualBase):
    def test_exports_mp4_and_caption_txt(self):
        video_id = self._make_video_with_file()
        dest = tm.export_for_manual_upload(video_id, queue_dir=self.queue)
        self.assertTrue(os.path.exists(dest))
        txt = dest.replace(".mp4", ".txt")
        with open(txt, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("Chuyện mẹ chồng", content)
        self.assertIn("#drama #mechong", content)

    def test_export_is_idempotent(self):
        video_id = self._make_video_with_file()
        first = tm.export_for_manual_upload(video_id, queue_dir=self.queue)
        second = tm.export_for_manual_upload(video_id, queue_dir=self.queue)
        self.assertEqual(first, second)
        day_dir = os.path.dirname(first)
        mp4s = [f for f in os.listdir(day_dir) if f.endswith(".mp4")]
        self.assertEqual(len(mp4s), 1)

    def test_missing_video_returns_none(self):
        self.assertIsNone(tm.export_for_manual_upload(999, queue_dir=self.queue))

    def test_missing_file_returns_none(self):
        video_id = db.insert_video(video_type="short", script_text="x")
        self.assertIsNone(tm.export_for_manual_upload(video_id, queue_dir=self.queue))

    def test_caption_falls_back_to_youtube_title(self):
        video_id = db.insert_video(video_type="short", script_text="x",
                                   youtube_title="Tiêu đề YT")
        src = os.path.join(self.tmp, "s.mp4")
        with open(src, "wb") as f:
            f.write(b"x")
        db.update_video_paths(video_id, video_path=src)
        dest = tm.export_for_manual_upload(video_id, queue_dir=self.queue)
        with open(dest.replace(".mp4", ".txt"), encoding="utf-8") as f:
            self.assertIn("Tiêu đề YT", f.read())


class TestListQueue(TikTokManualBase):
    def test_empty_queue(self):
        self.assertEqual(tm.list_queue(queue_dir=self.queue), {})

    def test_lists_by_day(self):
        video_id = self._make_video_with_file()
        tm.export_for_manual_upload(video_id, queue_dir=self.queue)
        result = tm.list_queue(queue_dir=self.queue)
        self.assertEqual(len(result), 1)
        (day, files), = result.items()
        self.assertEqual(files, [f"video_{video_id}.mp4"])


if __name__ == "__main__":
    unittest.main()
