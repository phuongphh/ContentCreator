"""Tests for processors/drama_rewriter.py (Phase 3 — Drama Generation Layer)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import storage.database as db
import storage.migrate as migrate
import storage.stories as stories
import processors.drama_rewriter as drama_rewriter


def _good_rewrite(word_count: int = 900, commentary_words: int = 220) -> dict:
    return {
        "title": "Sếp bắt làm thêm giờ không lương",
        "hook": "Ba năm đi làm, tôi chưa từng nghĩ mình sẽ bị đối xử như vậy.",
        "script": " ".join(["từ"] * word_count),
        "vn_commentary": " ".join(["bình_luận"] * commentary_words),
        "thumbnail_prompt": "office worker looking stressed, dramatic lighting",
        "tags": ["#drama", "#congso"],
    }


def _fake_message(json_dict: dict):
    msg = MagicMock()
    msg.usage = None
    msg.content = [MagicMock(text=json.dumps(json_dict, ensure_ascii=False))]
    return msg


def _mock_anthropic(json_dict: dict):
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _fake_message(json_dict)
    return patch.object(drama_rewriter.anthropic, "Anthropic", return_value=fake_client)


class TestValidateRewrite(unittest.TestCase):
    def test_good_rewrite_has_no_issues(self):
        self.assertEqual(drama_rewriter.validate_rewrite(_good_rewrite()), [])

    def test_missing_field_flagged(self):
        result = _good_rewrite()
        del result["vn_commentary"]
        issues = drama_rewriter.validate_rewrite(result)
        self.assertTrue(any("missing/empty" in i for i in issues))

    def test_script_too_short_flagged(self):
        issues = drama_rewriter.validate_rewrite(_good_rewrite(word_count=100))
        self.assertTrue(any("word count" in i for i in issues))

    def test_script_too_long_flagged(self):
        issues = drama_rewriter.validate_rewrite(_good_rewrite(word_count=2000))
        self.assertTrue(any("word count" in i for i in issues))

    def test_short_vn_commentary_flagged(self):
        issues = drama_rewriter.validate_rewrite(_good_rewrite(commentary_words=50))
        self.assertTrue(any("vn_commentary only" in i for i in issues))

    def test_overlong_hook_flagged(self):
        result = _good_rewrite()
        result["hook"] = " ".join(["từ"] * 40)  # a paragraph, not a 3s line
        issues = drama_rewriter.validate_rewrite(result)
        self.assertTrue(any("hook has" in i for i in issues))

    def test_short_hook_not_flagged(self):
        result = _good_rewrite()
        result["hook"] = "Ba năm đi làm, tôi chưa từng nghĩ mình sẽ bị đối xử như vậy."
        self.assertEqual(drama_rewriter.validate_rewrite(result), [])

    def test_western_name_fragment_flagged(self):
        result = _good_rewrite()
        result["title"] = "Linh Smith và câu chuyện của cô"
        issues = drama_rewriter.validate_rewrite(result)
        self.assertTrue(any("smith" in i for i in issues))

    def test_foreign_culture_term_flagged(self):
        result = _good_rewrite()
        result["script"] = result["script"] + " đi đến mall mua đồ"
        issues = drama_rewriter.validate_rewrite(result)
        self.assertTrue(any("mall" in i for i in issues))

    def test_dollar_symbol_flagged(self):
        result = _good_rewrite()
        result["script"] = result["script"] + " mất $500"
        issues = drama_rewriter.validate_rewrite(result)
        self.assertTrue(any("$" in i for i in issues))

    def test_empty_tags_flagged(self):
        result = _good_rewrite()
        result["tags"] = []
        issues = drama_rewriter.validate_rewrite(result)
        self.assertTrue(any("tags" in i for i in issues))

    def test_vietnamese_word_not_falsely_flagged_as_western_name(self):
        # Sanity check: word-boundary regex shouldn't misfire on ordinary
        # Vietnamese text that happens to contain a substring like "john".
        result = _good_rewrite()
        result["script"] = result["script"] + " không liên quan gì tới john cả"
        issues = drama_rewriter.validate_rewrite(result)
        # "john" IS a blacklisted fragment - this documents current behavior
        # (whole-word match, still triggers on the literal word "john").
        self.assertTrue(any("john" in i for i in issues))


class RewriterTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dbpath = os.path.join(self.tmp, "test.db")
        self._patch = patch.object(db.config, "DB_PATH", self.dbpath)
        self._patch.start()
        db.init_db()
        migrate.migrate_up()
        self.story_id = stories.insert_story(
            "reddit", "s1", "Some drama story content here.", track="drama",
        )
        stories.update_status(self.story_id, "pending", rubric_score=6)

    def tearDown(self):
        self._patch.stop()


class TestRewriteStory(RewriterTestBase):
    def test_valid_rewrite_sets_approved(self):
        with _mock_anthropic(_good_rewrite()):
            result = drama_rewriter.rewrite_story(self.story_id)
        self.assertIsNotNone(result)
        story = stories.get_story(self.story_id)
        self.assertEqual(story["status"], "approved")
        self.assertIn("Sếp bắt", story["rewritten_content"])

    def test_invalid_rewrite_sets_needs_review_and_alerts(self):
        bad = _good_rewrite(word_count=50)
        with _mock_anthropic(bad), \
             patch("notifier.telegram_bot.send_alert") as alert:
            result = drama_rewriter.rewrite_story(self.story_id)
        self.assertIsNotNone(result)
        story = stories.get_story(self.story_id)
        self.assertEqual(story["status"], "needs_review")
        # Output is still saved for human review, not discarded.
        self.assertIsNotNone(story["rewritten_content"])
        alert.assert_called_once()
        self.assertIn(str(self.story_id), alert.call_args[0][0])

    def test_missing_story_returns_none(self):
        self.assertIsNone(drama_rewriter.rewrite_story(999999))

    def test_malformed_json_leaves_story_untouched(self):
        fake_client = MagicMock()
        bad_message = MagicMock()
        bad_message.usage = None
        bad_message.content = [MagicMock(text="not json")]
        fake_client.messages.create.return_value = bad_message
        with patch.object(drama_rewriter.anthropic, "Anthropic", return_value=fake_client):
            result = drama_rewriter.rewrite_story(self.story_id)
        self.assertIsNone(result)
        story = stories.get_story(self.story_id)
        self.assertEqual(story["status"], "pending")
        self.assertIsNone(story["rewritten_content"])


class TestRewriteAllScored(RewriterTestBase):
    def test_skips_stories_without_rubric_score(self):
        unscored_id = stories.insert_story("reddit", "s2", "raw", track="drama")
        with patch.object(drama_rewriter, "rewrite_story") as mocked:
            drama_rewriter.rewrite_all_scored()
        mocked.assert_called_once_with(self.story_id)

    def test_skips_already_rewritten_stories(self):
        stories.update_status(self.story_id, "approved", rewritten_content="{}")
        with patch.object(drama_rewriter, "rewrite_story") as mocked:
            count = drama_rewriter.rewrite_all_scored()
        mocked.assert_not_called()
        self.assertEqual(count, 0)

    def test_rewrites_eligible_story(self):
        with _mock_anthropic(_good_rewrite()):
            count = drama_rewriter.rewrite_all_scored()
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
