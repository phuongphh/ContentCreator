"""Integration tests for the migration runner (Phase 1 — Multi-channel Foundation)."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import storage.database as db
import storage.migrate as migrate


class TestMigrateUpOnCleanCheckout(unittest.TestCase):
    """`migrate up` must work as the very first command on a brand-new DB
    file — no prior `init_db()` call — since that's the documented flow for
    a fresh checkout (README / CLAUDE.md just say `python -m storage.migrate up`).
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dbpath = os.path.join(self.tmp, "test.db")
        self._patch = patch.object(db.config, "DB_PATH", self.dbpath)
        self._patch.start()
        # Deliberately do NOT call db.init_db() — simulates a clean checkout
        # where only `python -m storage.migrate up` has ever been run.

    def tearDown(self):
        self._patch.stop()

    def test_up_creates_base_schema_then_applies_migration(self):
        applied = migrate.migrate_up()
        self.assertIn("001_multi_track", applied)
        conn = db.get_connection()
        try:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(articles)")}
        finally:
            conn.close()
        self.assertIn("track", cols)
        self.assertIn("destination", cols)


class TestMigrateUp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dbpath = os.path.join(self.tmp, "test.db")
        self._patch = patch.object(db.config, "DB_PATH", self.dbpath)
        self._patch.start()
        db.init_db()

    def tearDown(self):
        self._patch.stop()

    def test_up_applies_001(self):
        applied = migrate.migrate_up()
        self.assertIn("001_multi_track", applied)

    def test_up_is_idempotent(self):
        migrate.migrate_up()
        second_run = migrate.migrate_up()
        self.assertEqual(second_run, [])

    def test_track_column_added_with_default_ai(self):
        migrate.migrate_up()
        video_id = db.insert_video("long", "Script thử nghiệm.")
        conn = db.get_connection()
        try:
            row = conn.execute("SELECT track FROM videos WHERE id = ?", (video_id,)).fetchone()
        finally:
            conn.close()
        self.assertEqual(row["track"], "ai")

    def test_destination_column_added_nullable(self):
        migrate.migrate_up()
        conn = db.get_connection()
        try:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(videos)")}
        finally:
            conn.close()
        self.assertIn("destination", cols)

    def test_stories_table_created(self):
        migrate.migrate_up()
        conn = db.get_connection()
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='stories'"
            ).fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(row)

    def test_articles_track_column_added(self):
        migrate.migrate_up()
        conn = db.get_connection()
        try:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(articles)")}
        finally:
            conn.close()
        self.assertIn("track", cols)
        self.assertIn("destination", cols)


class TestMigrateAtomicity(unittest.TestCase):
    """A migration script that fails partway through must leave neither a
    partial schema change nor a bookkeeping row — schema DDL and the
    _migrations INSERT are one SQLite transaction (single executescript
    call), so a mid-script failure rolls both back together.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dbpath = os.path.join(self.tmp, "test.db")
        self._patch = patch.object(db.config, "DB_PATH", self.dbpath)
        self._patch.start()
        db.init_db()

        self.migrations_dir = Path(tempfile.mkdtemp())
        # Valid first statement, then a statement that references a
        # nonexistent table to force a mid-script failure.
        (self.migrations_dir / "001_broken.sql").write_text(
            "ALTER TABLE articles ADD COLUMN partial_col TEXT;\n"
            "ALTER TABLE table_that_does_not_exist ADD COLUMN x TEXT;\n",
            encoding="utf-8",
        )
        self._dir_patch = patch.object(migrate, "MIGRATIONS_DIR", self.migrations_dir)
        self._dir_patch.start()

    def tearDown(self):
        self._dir_patch.stop()
        self._patch.stop()

    def test_failed_migration_leaves_no_bookkeeping_row(self):
        with self.assertRaises(sqlite3.OperationalError):
            migrate.migrate_up()
        conn = db.get_connection()
        try:
            row = conn.execute(
                "SELECT 1 FROM _migrations WHERE version = '001_broken'"
            ).fetchone()
        finally:
            conn.close()
        self.assertIsNone(row)

    def test_failed_migration_rolls_back_partial_schema_change(self):
        with self.assertRaises(sqlite3.OperationalError):
            migrate.migrate_up()
        conn = db.get_connection()
        try:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(articles)")}
        finally:
            conn.close()
        self.assertNotIn("partial_col", cols)


class TestMigrateStatus(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dbpath = os.path.join(self.tmp, "test.db")
        self._patch = patch.object(db.config, "DB_PATH", self.dbpath)
        self._patch.start()
        db.init_db()

    def tearDown(self):
        self._patch.stop()

    def test_status_before_up_shows_pending(self):
        entries = migrate.status()
        self.assertTrue(any(e["version"] == "001_multi_track" and not e["applied"] for e in entries))

    def test_status_after_up_shows_applied(self):
        migrate.migrate_up()
        entries = migrate.status()
        self.assertTrue(any(e["version"] == "001_multi_track" and e["applied"] for e in entries))


class TestTrackDestinationRoundtrip(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dbpath = os.path.join(self.tmp, "test.db")
        self._patch = patch.object(db.config, "DB_PATH", self.dbpath)
        self._patch.start()
        db.init_db()

    def tearDown(self):
        self._patch.stop()

    def test_insert_video_before_migration_falls_back_gracefully(self):
        # No migration applied yet: track/destination columns don't exist,
        # insert_video must still succeed via its legacy-INSERT fallback.
        video_id = db.insert_video("short", "Kịch bản.")
        self.assertIsInstance(video_id, int)

    def test_insert_video_after_migration_stores_custom_track(self):
        migrate.migrate_up()
        video_id = db.insert_video("short", "Kịch bản.", track="drama",
                                   destination="drama_youtube")
        video = db.get_video(video_id)
        self.assertEqual(video["track"], "drama")
        self.assertEqual(video["destination"], "drama_youtube")

    def test_insert_article_after_migration_stores_custom_track(self):
        migrate.migrate_up()
        article_id = db.insert_article("reddit", "Tiêu đề", "https://example.com/1",
                                       track="drama", destination="tiktok_main")
        conn = db.get_connection()
        try:
            row = conn.execute(
                "SELECT track, destination FROM articles WHERE id = ?", (article_id,)
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row["track"], "drama")
        self.assertEqual(row["destination"], "tiktok_main")


class TestMigrateDown(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dbpath = os.path.join(self.tmp, "test.db")
        self._patch = patch.object(db.config, "DB_PATH", self.dbpath)
        self._patch.start()
        db.init_db()
        migrate.migrate_up()

    def tearDown(self):
        self._patch.stop()

    def test_down_removes_stories_table(self):
        migrate.migrate_down()
        conn = db.get_connection()
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='stories'"
            ).fetchone()
        finally:
            conn.close()
        self.assertIsNone(row)

    def test_down_then_up_again_is_clean(self):
        migrate.migrate_down()
        applied = migrate.migrate_up()
        self.assertIn("001_multi_track", applied)


if __name__ == "__main__":
    unittest.main()
