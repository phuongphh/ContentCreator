"""Tests for the internal per-channel video sequence number (channel_seq)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import storage.database as db
import storage.migrate as migrate

UP = "011_video_channel_seq"


def _publish(video_id):
    db.update_video_status(video_id, "published")


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dbpath = os.path.join(self.tmp, "test.db")
        self._patch = patch.object(db.config, "DB_PATH", self.dbpath)
        self._patch.start()
        db.init_db()

    def tearDown(self):
        self._patch.stop()


class TestChannelSeq(_Base):
    def setUp(self):
        super().setUp()
        migrate.migrate_up()

    def test_ai_starts_at_138_and_increments(self):  # AC1
        a = db.insert_video("short", "s", track="ai")
        self.assertEqual(db.get_video_channel_seq(a), 138)
        _publish(a)
        b = db.insert_video("short", "s", track="ai")
        self.assertEqual(db.get_video_channel_seq(b), 139)
        c = db.insert_video("short", "s", track="ai")
        self.assertEqual(db.get_video_channel_seq(c), 140)

    def test_drama_continues_posted_count_independent(self):  # AC2
        ids = [db.insert_video("short", "s", track="drama") for _ in range(3)]
        for i in ids:
            _publish(i)
        d = db.insert_video("short", "s", track="drama")
        self.assertEqual(db.get_video_channel_seq(d), 4)
        ai = db.insert_video("short", "s", track="ai")
        self.assertEqual(db.get_video_channel_seq(ai), 138)

    def test_seq_not_in_youtube_body(self):  # AC3
        from publisher import youtube_uploader as yu
        vid = db.insert_video("short", "s", youtube_title="Tiêu đề gốc",
                              track="ai", destination="ai_youtube")
        video = db.get_video(vid)
        body = yu._build_video_body(video, "ai_youtube")
        self.assertEqual(body["snippet"]["title"], "Tiêu đề gốc #Shorts")
        self.assertNotIn("138", body["snippet"]["title"])
        self.assertNotIn("138", body["snippet"]["description"])
        self.assertNotIn("138", " ".join(body["snippet"]["tags"]))


class TestLegacyAndMigration(_Base):
    def test_legacy_rows_untouched(self):  # AC4
        migrate.migrate_up()
        # simulate legacy rows: insert then null out is not allowed; insert via SQL
        conn = db.get_connection()
        conn.execute("INSERT INTO videos (video_type, script_text, track, status) "
                     "VALUES ('short', 'old', 'ai', 'published')")
        conn.commit()
        conn.close()
        before = db.get_video(1)
        new_id = db.insert_video("short", "new", track="ai")
        self.assertEqual(db.get_video_channel_seq(new_id), 139)
        self.assertEqual(db.get_video(1), before)
        self.assertIsNone(db.get_video_channel_seq(1))

    def test_up_down_preserves_data(self):  # AC5
        # apply everything before 011
        migrate.migrate_up()
        migrate.migrate_down()  # revert 011 (latest) if it is latest
        while any(e["version"] == UP and e["applied"] for e in migrate.status()):
            migrate.migrate_down()
        conn = db.get_connection()
        conn.execute("INSERT INTO videos (video_type, script_text, track) "
                     "VALUES ('short', 'old', 'ai')")
        conn.commit()
        before = [tuple(r) for r in conn.execute(
            "SELECT id, video_type, script_text, track, status FROM videos")]
        conn.close()
        migrate.migrate_up()
        migrate.migrate_down()
        while any(e["version"] == UP and e["applied"] for e in migrate.status()):
            migrate.migrate_down()
        conn = db.get_connection()
        after = [tuple(r) for r in conn.execute(
            "SELECT id, video_type, script_text, track, status FROM videos")]
        conn.close()
        self.assertEqual(before, after)
        down = migrate.MIGRATIONS_DIR / f"{UP}_down.sql"
        self.assertTrue(down.exists())


if __name__ == "__main__":
    unittest.main()
