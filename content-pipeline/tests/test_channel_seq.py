"""Tests for per-channel creation-order sequence (channel_seq)."""
from __future__ import annotations

import inspect
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import storage.database as db
import storage.migrate as migrate


def _seed_published(track: str, n: int) -> None:
    conn = db.get_connection()
    try:
        for i in range(n):
            conn.execute(
                "INSERT INTO videos (video_type, script_text, status, track) "
                "VALUES ('short', ?, 'published', ?)",
                (f"old {i}", track),
            )
        conn.commit()
    finally:
        conn.close()


class TestChannelSeq(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dbpath = os.path.join(self.tmp, "test.db")
        self._patch = patch.object(db.config, "DB_PATH", self.dbpath)
        self._patch.start()
        db.init_db()

    def tearDown(self):
        self._patch.stop()

    def test_ai_first_is_138_despite_published(self):
        migrate.migrate_up()
        _seed_published("ai", 143)
        vid = db.insert_video("short", "script", track="ai")
        self.assertEqual(db.get_video(vid)["channel_seq"], 138)

    def test_consecutive_numbers(self):
        migrate.migrate_up()
        a1 = db.insert_video("short", "s", track="ai")
        a2 = db.insert_video("short", "s", track="ai")
        d1 = db.insert_video("short", "s", track="drama")
        d2 = db.insert_video("short", "s", track="drama")
        self.assertEqual((db.get_video(a1)["channel_seq"], db.get_video(a2)["channel_seq"]), (138, 139))
        self.assertEqual((db.get_video(d1)["channel_seq"], db.get_video(d2)["channel_seq"]), (114, 115))

    def test_drama_first_is_114_despite_published(self):
        migrate.migrate_up()
        _seed_published("drama", 107)
        vid = db.insert_video("short", "script", track="drama")
        self.assertEqual(db.get_video(vid)["channel_seq"], 114)

    def test_unique_partial_index_and_single_transaction(self):
        migrate.migrate_up()
        conn = db.get_connection()
        try:
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' "
                "AND name='ux_videos_track_channel_seq'"
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertIn("channel_seq IS NOT NULL", row["sql"])
            conn.execute(
                "INSERT INTO videos (video_type, script_text, track, channel_seq) "
                "VALUES ('short','x','ai',500)"
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO videos (video_type, script_text, track, channel_seq) "
                    "VALUES ('short','y','ai',500)"
                )
        finally:
            conn.close()
        src = inspect.getsource(db.insert_video)
        self.assertIn("BEGIN IMMEDIATE", src)
        self.assertIn("_next_channel_seq(conn", src)
        self.assertEqual(src.count("get_connection()"), 1)

    def test_old_rows_stay_null(self):
        conn = db.get_connection()
        try:
            conn.execute(
                "INSERT INTO videos (video_type, script_text, status) "
                "VALUES ('short','legacy','published')"
            )
            conn.commit()
        finally:
            conn.close()
        migrate.migrate_up()
        vid = db.insert_video("short", "new", track="ai")
        self.assertEqual(db.get_video(vid)["channel_seq"], 138)
        conn = db.get_connection()
        try:
            row = conn.execute(
                "SELECT channel_seq, script_text, status FROM videos WHERE script_text='legacy'"
            ).fetchone()
        finally:
            conn.close()
        self.assertIsNone(row["channel_seq"])
        self.assertEqual(row["status"], "published")

    def test_seq_not_in_youtube_body(self):
        migrate.migrate_up()
        _seed_published("ai", 3)
        vid = db.insert_video("short", "s", youtube_title="Tin AI moi",
                              youtube_description="Mo ta", tiktok_hashtags="#ai",
                              track="ai")
        video = db.get_video(vid)
        from publisher.youtube_uploader import _build_video_body
        body = _build_video_body(video, "ai_youtube")
        seq = str(video["channel_seq"])
        self.assertEqual(seq, "138")
        sn = body["snippet"]
        self.assertNotIn(seq, sn["title"])
        self.assertNotIn(seq, sn["description"])
        self.assertFalse(any(seq in t for t in sn["tags"]))


if __name__ == "__main__":
    unittest.main()
