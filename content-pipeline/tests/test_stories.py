"""Tests for storage/stories.py (Phase 2 — Drama Source Layer)."""
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
import storage.stories as stories


class StoriesTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dbpath = os.path.join(self.tmp, "test.db")
        self._patch = patch.object(db.config, "DB_PATH", self.dbpath)
        self._patch.start()
        db.init_db()
        migrate.migrate_up()

    def tearDown(self):
        self._patch.stop()


class TestCountProducible(StoriesTestBase):
    def test_counts_pending_and_approved_only(self):
        stories.insert_story("vn_original", "s1", "a")  # pending (default)
        sid2 = stories.insert_story("vn_original", "s2", "b")
        stories.update_status(sid2, "approved")
        sid3 = stories.insert_story("vn_original", "s3", "c")
        stories.update_status(sid3, "produced")  # not producible
        sid4 = stories.insert_story("vn_original", "s4", "d")
        stories.update_status(sid4, "rejected")  # not producible
        self.assertEqual(stories.count_producible("drama"), 2)  # s1 + s2

    def test_zero_when_empty(self):
        self.assertEqual(stories.count_producible("drama"), 0)

    def test_scoped_by_track(self):
        stories.insert_story("vn_original", "d1", "a", track="drama")
        stories.insert_story("rss", "a1", "b", track="ai")
        self.assertEqual(stories.count_producible("drama"), 1)
        self.assertEqual(stories.count_producible("ai"), 1)


class TestInsertStory(StoriesTestBase):
    def test_insert_returns_id(self):
        story_id = stories.insert_story("reddit", "abc1", "raw text")
        self.assertIsInstance(story_id, int)

    def test_default_track_is_drama(self):
        story_id = stories.insert_story("reddit", "abc2", "raw text")
        story = stories.get_story(story_id)
        self.assertEqual(story["track"], "drama")

    def test_default_status_is_pending(self):
        story_id = stories.insert_story("reddit", "abc3", "raw text")
        story = stories.get_story(story_id)
        self.assertEqual(story["status"], "pending")

    def test_metadata_roundtrips_as_dict(self):
        story_id = stories.insert_story(
            "reddit", "abc4", "raw text",
            metadata={"subreddit": "AmItheAsshole", "upvotes": 5000},
        )
        story = stories.get_story(story_id)
        self.assertEqual(story["metadata"], {"subreddit": "AmItheAsshole", "upvotes": 5000})

    def test_no_metadata_stays_none(self):
        story_id = stories.insert_story("reddit", "abc5", "raw text")
        story = stories.get_story(story_id)
        self.assertIsNone(story["metadata"])

    def test_title_stored(self):
        story_id = stories.insert_story("reddit", "abc6", "raw text", title="Some title")
        story = stories.get_story(story_id)
        self.assertEqual(story["title"], "Some title")

    def test_duplicate_source_id_raises(self):
        stories.insert_story("reddit", "dup1", "first")
        with self.assertRaises(sqlite3.IntegrityError):
            stories.insert_story("reddit", "dup1", "second")

    def test_vn_original_without_source_id(self):
        # Manual VN seeds may not have a natural source_id (None allowed,
        # multiple NULLs don't violate the unique index).
        id1 = stories.insert_story("vn_original", None, "seed 1")
        id2 = stories.insert_story("vn_original", None, "seed 2")
        self.assertNotEqual(id1, id2)


class TestDedupeCheck(StoriesTestBase):
    def test_true_for_existing(self):
        stories.insert_story("reddit", "exists1", "raw")
        self.assertTrue(stories.dedupe_check("exists1"))

    def test_false_for_missing(self):
        self.assertFalse(stories.dedupe_check("does-not-exist"))


class TestGetPending(StoriesTestBase):
    def test_only_pending_returned(self):
        id1 = stories.insert_story("reddit", "p1", "raw")
        stories.update_status(id1, "approved")
        id2 = stories.insert_story("reddit", "p2", "raw")
        pending = stories.get_pending()
        ids = {s["id"] for s in pending}
        self.assertIn(id2, ids)
        self.assertNotIn(id1, ids)

    def test_respects_limit(self):
        for i in range(5):
            stories.insert_story("reddit", f"limit{i}", "raw")
        pending = stories.get_pending(limit=2)
        self.assertEqual(len(pending), 2)

    def test_filters_by_track(self):
        stories.insert_story("reddit", "drama1", "raw", track="drama")
        stories.insert_story("reddit", "ai1", "raw", track="ai")
        pending = stories.get_pending(track="ai")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["source_id"], "ai1")

    def test_sorted_newest_first(self):
        id1 = stories.insert_story("reddit", "old", "raw")
        id2 = stories.insert_story("reddit", "new", "raw")
        pending = stories.get_pending()
        self.assertEqual(pending[0]["id"], id2)
        self.assertEqual(pending[1]["id"], id1)


class TestGetByStatus(StoriesTestBase):
    def test_returns_matching_status_only(self):
        id1 = stories.insert_story("reddit", "g1", "raw")
        stories.update_status(id1, "produced")
        id2 = stories.insert_story("reddit", "g2", "raw")
        stories.update_status(id2, "produced")
        stories.insert_story("reddit", "g3", "raw")  # stays pending

        produced = stories.get_by_status("produced")
        ids = {s["id"] for s in produced}
        self.assertEqual(ids, {id1, id2})

    def test_empty_when_no_match(self):
        stories.insert_story("reddit", "g4", "raw")
        self.assertEqual(stories.get_by_status("produced"), [])

    def test_get_pending_is_shortcut_for_pending_status(self):
        stories.insert_story("reddit", "g5", "raw")
        self.assertEqual(stories.get_pending(), stories.get_by_status("pending"))


class TestUpdateStatus(StoriesTestBase):
    def test_updates_status(self):
        story_id = stories.insert_story("reddit", "u1", "raw")
        stories.update_status(story_id, "approved")
        self.assertEqual(stories.get_story(story_id)["status"], "approved")

    def test_updates_extra_field(self):
        story_id = stories.insert_story("reddit", "u2", "raw")
        stories.update_status(story_id, "approved", rubric_score=5)
        story = stories.get_story(story_id)
        self.assertEqual(story["status"], "approved")
        self.assertEqual(story["rubric_score"], 5)

    def test_updates_multiple_extra_fields(self):
        story_id = stories.insert_story("reddit", "u3", "raw")
        stories.update_status(story_id, "produced", rubric_score=6,
                              rewritten_content="{}", destination="drama_youtube")
        story = stories.get_story(story_id)
        self.assertEqual(story["rubric_score"], 6)
        self.assertEqual(story["rewritten_content"], "{}")
        self.assertEqual(story["destination"], "drama_youtube")

    def test_unknown_field_raises(self):
        story_id = stories.insert_story("reddit", "u4", "raw")
        with self.assertRaises(ValueError):
            stories.update_status(story_id, "approved", source="evil; DROP TABLE stories")


class TestGetStory(StoriesTestBase):
    def test_missing_returns_none(self):
        self.assertIsNone(stories.get_story(999999))


if __name__ == "__main__":
    unittest.main()
