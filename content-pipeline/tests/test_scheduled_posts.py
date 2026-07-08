"""Tests for storage/scheduled_posts.py (Phase 5 — Distribution)."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import storage.database as db
import storage.migrate as migrate
import storage.scheduled_posts as sp


class ScheduledPostsBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dbpath = os.path.join(self.tmp, "test.db")
        self._patch = patch.object(db.config, "DB_PATH", self.dbpath)
        self._patch.start()
        db.init_db()
        migrate.migrate_up()

    def tearDown(self):
        self._patch.stop()


class TestInsertAndGet(ScheduledPostsBase):
    def test_insert_returns_id(self):
        post_id = sp.insert_post(1, "drama_youtube", "2026-07-08 12:00:00")
        self.assertIsInstance(post_id, int)
        post = sp.get_post(post_id)
        self.assertEqual(post["status"], "queued")
        self.assertEqual(post["channel_key"], "drama_youtube")

    def test_slot_unique_for_active_posts(self):
        sp.insert_post(1, "drama_youtube", "2026-07-08 12:00:00")
        with self.assertRaises(sqlite3.IntegrityError):
            sp.insert_post(2, "drama_youtube", "2026-07-08 12:00:00")

    def test_same_slot_ok_after_failed(self):
        post_id = sp.insert_post(1, "drama_youtube", "2026-07-08 12:00:00")
        sp.mark_failed(post_id, "boom")
        # slot giải phóng khi post trước failed
        sp.insert_post(2, "drama_youtube", "2026-07-08 12:00:00")

    def test_video_channel_unique_while_active(self):
        sp.insert_post(1, "drama_youtube", "2026-07-08 12:00:00")
        with self.assertRaises(sqlite3.IntegrityError):
            sp.insert_post(1, "drama_youtube", "2026-07-08 21:00:00")

    def test_same_video_other_channel_ok(self):
        sp.insert_post(1, "drama_youtube", "2026-07-08 12:00:00")
        sp.insert_post(1, "tiktok_main", "2026-07-08 12:00:00")


class TestClaim(ScheduledPostsBase):
    def test_claim_once(self):
        post_id = sp.insert_post(1, "drama_youtube", "2026-07-08 12:00:00")
        self.assertTrue(sp.claim(post_id))
        self.assertFalse(sp.claim(post_id))  # đã uploading — lần 2 thua
        self.assertEqual(sp.get_post(post_id)["status"], "uploading")

    def test_mark_done_records_platform_id(self):
        post_id = sp.insert_post(1, "drama_youtube", "2026-07-08 12:00:00")
        sp.claim(post_id)
        sp.mark_done(post_id, platform_video_id="yt123", url="https://youtu.be/yt123")
        post = sp.get_post(post_id)
        self.assertEqual(post["status"], "done")
        self.assertEqual(post["platform_video_id"], "yt123")

    def test_record_platform_id_keeps_uploading_status(self):
        # Bằng chứng "đã live" phải ghi được TRƯỚC khi mark_done (chống mất
        # dấu khi crash giữa thumbnail/caption — finding Codex PR #70).
        post_id = sp.insert_post(1, "drama_youtube", "2026-07-08 12:00:00")
        sp.claim(post_id)
        sp.record_platform_id(post_id, "yt9", url="https://youtu.be/yt9")
        post = sp.get_post(post_id)
        self.assertEqual(post["status"], "uploading")
        self.assertEqual(post["platform_video_id"], "yt9")

    def test_mark_failed_keeps_platform_id(self):
        post_id = sp.insert_post(1, "drama_youtube", "2026-07-08 12:00:00")
        sp.claim(post_id)
        sp.record_platform_id(post_id, "yt9")
        sp.mark_failed(post_id, "thumbnail step died")
        post = sp.get_post(post_id)
        self.assertEqual(post["status"], "failed")
        self.assertEqual(post["platform_video_id"], "yt9")

    def test_mark_failed_records_error(self):
        post_id = sp.insert_post(1, "drama_youtube", "2026-07-08 12:00:00")
        sp.mark_failed(post_id, "quota exceeded")
        self.assertEqual(sp.get_post(post_id)["error"], "quota exceeded")


class TestQueries(ScheduledPostsBase):
    def test_get_due_only_past_queued(self):
        sp.insert_post(1, "drama_youtube", "2026-07-08 12:00:00")
        sp.insert_post(2, "drama_youtube", "2099-01-01 12:00:00")
        due = sp.get_due(now="2026-07-08 12:03:00")
        self.assertEqual([p["video_id"] for p in due], [1])

    def test_slot_taken(self):
        sp.insert_post(1, "drama_youtube", "2026-07-08 12:00:00")
        self.assertTrue(sp.slot_taken("drama_youtube", "2026-07-08 12:00:00"))
        self.assertFalse(sp.slot_taken("drama_youtube", "2026-07-08 21:00:00"))
        self.assertFalse(sp.slot_taken("ai_youtube", "2026-07-08 12:00:00"))

    def test_find_active(self):
        post_id = sp.insert_post(1, "drama_youtube", "2026-07-08 12:00:00")
        found = sp.find_active(1, "drama_youtube")
        self.assertEqual(found["id"], post_id)
        self.assertIsNone(sp.find_active(1, "ai_youtube"))
        sp.mark_failed(post_id, "x")
        self.assertIsNone(sp.find_active(1, "drama_youtube"))

    def test_find_active_includes_done(self):
        post_id = sp.insert_post(1, "drama_youtube", "2026-07-08 12:00:00")
        sp.claim(post_id)
        sp.mark_done(post_id, "yt1", "https://youtu.be/yt1")
        # done vẫn "active" — video đã lên kênh, không được xếp lịch lại
        self.assertIsNotNone(sp.find_active(1, "drama_youtube"))

    def test_stale_uploading(self):
        post_id = sp.insert_post(1, "drama_youtube", "2026-07-08 12:00:00")
        sp.claim(post_id)
        # tươi mới → chưa stale
        self.assertEqual(sp.get_stale_uploading(older_than_minutes=90), [])
        # già hoá updated_at trực tiếp
        conn = db.get_connection()
        conn.execute("UPDATE scheduled_posts SET updated_at = '2020-01-01 00:00:00' "
                     "WHERE id = ?", (post_id,))
        conn.commit()
        conn.close()
        stale = sp.get_stale_uploading(older_than_minutes=90)
        self.assertEqual([p["id"] for p in stale], [post_id])

    def test_count_by_status(self):
        sp.insert_post(1, "drama_youtube", "2026-07-08 12:00:00")
        p2 = sp.insert_post(2, "drama_youtube", "2026-07-08 21:00:00")
        sp.mark_failed(p2, "x")
        self.assertEqual(sp.count_by_status(), {"queued": 1, "failed": 1})


if __name__ == "__main__":
    unittest.main()
