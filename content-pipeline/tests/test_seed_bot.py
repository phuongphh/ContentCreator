"""Tests for notifier/seed_bot.py (Phase 2 — Drama Source Layer)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import notifier.seed_bot as seed_bot
import storage.database as db
import storage.migrate as migrate


class SeedBotTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dbpath = os.path.join(self.tmp, "test.db")
        self._db_patch = patch.object(db.config, "DB_PATH", self.dbpath)
        self._db_patch.start()
        db.init_db()
        migrate.migrate_up()

        # Isolate the conversation-state file per test so tests don't leak
        # awaiting-state into each other via the real notifier/ directory.
        self.state_file = os.path.join(self.tmp, ".seed_state.json")
        self._state_patch = patch.object(seed_bot, "_STATE_FILE", self.state_file)
        self._state_patch.start()

    def tearDown(self):
        self._state_patch.stop()
        self._db_patch.stop()


class TestSeedVnFlow(SeedBotTestBase):
    def test_start_returns_prompt_and_sets_state(self):
        reply = seed_bot.start_seed_vn()
        self.assertIn("tình huống lõi", reply)
        self.assertEqual(seed_bot._get_awaiting(), "vn_seed")

    def test_message_after_start_is_saved(self):
        seed_bot.start_seed_vn()
        reply = seed_bot.handle_awaiting_message("Sếp bắt tôi làm thêm giờ không lương.")
        self.assertIn("Đã lưu", reply)
        self.assertIsNone(seed_bot._get_awaiting())  # state cleared after use

    def test_saved_story_has_expected_fields(self):
        seed_bot.start_seed_vn()
        seed_bot.handle_awaiting_message("Một tình huống drama.")
        from storage.stories import get_pending
        pending = get_pending(track="drama")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["source"], "vn_original")
        self.assertEqual(pending[0]["raw_content"], "Một tình huống drama.")
        self.assertTrue(pending[0]["source_id"].startswith("vn_"))

    def test_empty_message_not_saved(self):
        seed_bot.start_seed_vn()
        reply = seed_bot.handle_awaiting_message("   ")
        self.assertIn("trống", reply)
        from storage.stories import get_pending
        self.assertEqual(get_pending(track="drama"), [])

    def test_two_seeds_get_distinct_ids(self):
        seed_bot.start_seed_vn()
        seed_bot.handle_awaiting_message("Seed 1")
        seed_bot.start_seed_vn()
        seed_bot.handle_awaiting_message("Seed 2")
        from storage.stories import get_pending
        self.assertEqual(len(get_pending(track="drama")), 2)


class TestSeedUrlFlow(SeedBotTestBase):
    def test_invalid_url_rejected(self):
        seed_bot.start_seed_url()
        reply = seed_bot.handle_awaiting_message("not a url")
        self.assertIn("hợp lệ", reply)
        from storage.stories import get_pending
        self.assertEqual(get_pending(track="drama"), [])

    def test_valid_url_saved_with_og_metadata(self):
        fake_resp = MagicMock()
        fake_resp.text = (
            '<html><head>'
            '<meta content="Some Title" property="og:title">'
            '<meta property="og:description" content="Some description here">'
            '</head></html>'
        )
        fake_resp.raise_for_status = MagicMock()
        with patch("requests.get", return_value=fake_resp):
            seed_bot.start_seed_url()
            reply = seed_bot.handle_awaiting_message("https://facebook.com/some/post")
        self.assertIn("Đã lưu", reply)
        self.assertIn("Some Title", reply)

        from storage.stories import get_pending
        pending = get_pending(track="drama")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["title"], "Some Title")
        self.assertEqual(pending[0]["raw_content"], "Some description here")
        self.assertEqual(pending[0]["metadata"]["url"], "https://facebook.com/some/post")

    def test_fetch_failure_still_saves_seed(self):
        with patch("requests.get", side_effect=Exception("network down")):
            seed_bot.start_seed_url()
            reply = seed_bot.handle_awaiting_message("https://tiktok.com/@x/video/1")
        self.assertIn("Đã lưu", reply)
        from storage.stories import get_pending
        pending = get_pending(track="drama")
        self.assertEqual(len(pending), 1)
        # No OG title available -> falls back to the raw URL as content.
        self.assertEqual(pending[0]["raw_content"], "https://tiktok.com/@x/video/1")

    def test_same_url_submitted_twice_is_deduped(self):
        # Regression test: source_id used to be a random UUID per submission,
        # so resubmitting the same link created a duplicate row every time
        # instead of being caught by the source_id unique index/dedupe_check.
        with patch("requests.get", side_effect=Exception("network down")):
            seed_bot.start_seed_url()
            first_reply = seed_bot.handle_awaiting_message("https://facebook.com/same/post")
            seed_bot.start_seed_url()
            second_reply = seed_bot.handle_awaiting_message("https://facebook.com/same/post")

        self.assertIn("Đã lưu", first_reply)
        self.assertIn("đã được lưu", second_reply)
        from storage.stories import get_pending
        self.assertEqual(len(get_pending(track="drama")), 1)

    def test_source_id_is_deterministic_for_same_url(self):
        id1 = seed_bot._seed_url_source_id("https://facebook.com/x")
        id2 = seed_bot._seed_url_source_id("https://facebook.com/x")
        self.assertEqual(id1, id2)

    def test_source_id_ignores_trailing_slash(self):
        id1 = seed_bot._seed_url_source_id("https://facebook.com/x")
        id2 = seed_bot._seed_url_source_id("https://facebook.com/x/")
        self.assertEqual(id1, id2)

    def test_source_id_differs_for_different_urls(self):
        id1 = seed_bot._seed_url_source_id("https://facebook.com/x")
        id2 = seed_bot._seed_url_source_id("https://facebook.com/y")
        self.assertNotEqual(id1, id2)


class TestHandleAwaitingMessageNoop(SeedBotTestBase):
    def test_returns_none_when_nothing_awaiting(self):
        self.assertIsNone(seed_bot.handle_awaiting_message("just a regular message"))


class TestListPendingText(SeedBotTestBase):
    def test_empty_state(self):
        text = seed_bot.list_pending_text()
        self.assertIn("Không có story", text)

    def test_lists_pending_stories(self):
        from storage.stories import insert_story
        insert_story("reddit", "r1", "raw content here", track="drama", title="A drama title")
        text = seed_bot.list_pending_text()
        self.assertIn("A drama title", text)

    def test_respects_limit(self):
        from storage.stories import insert_story
        for i in range(10):
            insert_story("reddit", f"r{i}", "raw", track="drama")
        text = seed_bot.list_pending_text(limit=3)
        self.assertIn("3 story", text)

    def test_falls_back_to_raw_content_when_no_title(self):
        from storage.stories import insert_story
        insert_story("vn_original", "v1", "no title here just raw content", track="drama")
        text = seed_bot.list_pending_text()
        self.assertIn("no title here", text)


class TestHelpText(unittest.TestCase):
    def test_mentions_all_commands(self):
        text = seed_bot.help_text()
        for cmd in ("/seed_vn", "/seed_url", "/list_pending"):
            self.assertIn(cmd, text)


if __name__ == "__main__":
    unittest.main()
