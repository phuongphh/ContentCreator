"""Tests for the persisted subtitles_burned flag on videos (DB roundtrip)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import storage.database as db


class TestSubtitlesBurnedFlag(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dbpath = os.path.join(self.tmp, "test.db")
        self._patch = patch.object(db.config, "DB_PATH", self.dbpath)
        self._patch.start()
        db.init_db()
        self.vid = db.insert_video("long", "Một script thử nghiệm.")

    def tearDown(self):
        self._patch.stop()

    def test_column_exists(self):
        conn = db.get_connection()
        try:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(videos)")}
        finally:
            conn.close()
        self.assertIn("subtitles_burned", cols)

    def test_default_is_none(self):
        # NULL = unknown → publish falls back to config inference (legacy rows).
        self.assertIsNone(db.get_video(self.vid)["subtitles_burned"])

    def test_set_true(self):
        db.set_video_subtitles_burned(self.vid, True)
        self.assertEqual(db.get_video(self.vid)["subtitles_burned"], 1)

    def test_set_false(self):
        db.set_video_subtitles_burned(self.vid, False)
        self.assertEqual(db.get_video(self.vid)["subtitles_burned"], 0)

    def test_init_db_idempotent(self):
        # Running init_db again must not error or drop the flag.
        db.set_video_subtitles_burned(self.vid, True)
        db.init_db()
        self.assertEqual(db.get_video(self.vid)["subtitles_burned"], 1)


if __name__ == "__main__":
    unittest.main()
