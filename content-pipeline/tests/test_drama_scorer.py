"""Tests for processors/drama_scorer.py (Phase 3 — Drama Generation Layer)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import storage.database as db
import storage.migrate as migrate
import storage.stories as stories
import processors.drama_scorer as drama_scorer


def _fake_message(text: str):
    msg = MagicMock()
    msg.content = [MagicMock(text=text)]
    return msg


def _mock_anthropic(json_text: str):
    """Patch drama_scorer.anthropic.Anthropic so .messages.create(...) returns json_text."""
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _fake_message(json_text)
    return patch.object(drama_scorer.anthropic, "Anthropic", return_value=fake_client)


class TestValidateAndNormalizeRubric(unittest.TestCase):
    def test_recomputes_total_from_fields_ignoring_models_own_total(self):
        result = {
            "hook_3s": 1, "stakes": 1, "twist": 1,
            "localizable": 1, "comment_bait": 1, "safe": 1,
            "total": 999,  # model's own (wrong) arithmetic — must be ignored
        }
        normalized = drama_scorer._validate_and_normalize_rubric(result)
        self.assertEqual(normalized["total"], 6)

    def test_missing_field_raises(self):
        with self.assertRaises(ValueError):
            drama_scorer._validate_and_normalize_rubric({"hook_3s": 1})

    def test_non_binary_field_raises(self):
        bad = {"hook_3s": 2, "stakes": 1, "twist": 1,
               "localizable": 1, "comment_bait": 1, "safe": 1}
        with self.assertRaises(ValueError):
            drama_scorer._validate_and_normalize_rubric(bad)

    def test_default_reason_is_empty_string(self):
        result = {"hook_3s": 1, "stakes": 1, "twist": 1,
                  "localizable": 1, "comment_bait": 1, "safe": 1}
        normalized = drama_scorer._validate_and_normalize_rubric(result)
        self.assertEqual(normalized["reason"], "")


class ScorerTestBase(unittest.TestCase):
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

    def tearDown(self):
        self._patch.stop()


class TestScoreStory(ScorerTestBase):
    def test_passing_story_stays_pending_with_score(self):
        json_text = ('{"hook_3s":1,"stakes":1,"twist":1,"localizable":1,'
                     '"comment_bait":1,"safe":1,"reason":"good"}')
        with _mock_anthropic(json_text):
            result = drama_scorer.score_story(self.story_id)
        self.assertEqual(result["total"], 6)
        story = stories.get_story(self.story_id)
        self.assertEqual(story["status"], "pending")
        self.assertEqual(story["rubric_score"], 6)

    def test_low_score_rejected(self):
        json_text = ('{"hook_3s":0,"stakes":0,"twist":0,"localizable":1,'
                     '"comment_bait":0,"safe":1,"reason":"meh"}')
        with _mock_anthropic(json_text):
            drama_scorer.score_story(self.story_id)
        story = stories.get_story(self.story_id)
        self.assertEqual(story["status"], "rejected")

    def test_unsafe_story_rejected_even_with_high_total(self):
        json_text = ('{"hook_3s":1,"stakes":1,"twist":1,"localizable":1,'
                     '"comment_bait":1,"safe":0,"reason":"nsfw"}')
        with _mock_anthropic(json_text):
            result = drama_scorer.score_story(self.story_id)
        self.assertEqual(result["total"], 5)  # 5/6 would normally pass
        story = stories.get_story(self.story_id)
        self.assertEqual(story["status"], "rejected")  # but safe=0 vetoes it

    def test_missing_story_returns_none(self):
        self.assertIsNone(drama_scorer.score_story(999999))

    def test_malformed_json_leaves_story_untouched_for_retry(self):
        with _mock_anthropic("this is not json at all"):
            result = drama_scorer.score_story(self.story_id)
        self.assertIsNone(result)
        story = stories.get_story(self.story_id)
        self.assertEqual(story["status"], "pending")
        self.assertIsNone(story["rubric_score"])


class TestScoreAllPending(ScorerTestBase):
    def test_skips_already_scored_stories(self):
        stories.update_status(self.story_id, "pending", rubric_score=6)
        with patch.object(drama_scorer, "score_story") as mocked:
            count = drama_scorer.score_all_pending()
        mocked.assert_not_called()
        self.assertEqual(count, 0)

    def test_scores_unscored_pending_story(self):
        json_text = ('{"hook_3s":1,"stakes":1,"twist":1,"localizable":1,'
                     '"comment_bait":1,"safe":1}')
        with _mock_anthropic(json_text):
            count = drama_scorer.score_all_pending()
        self.assertEqual(count, 1)
        self.assertEqual(stories.get_story(self.story_id)["rubric_score"], 6)

    def test_ignores_non_drama_track(self):
        stories.insert_story("reddit", "ai1", "an AI story", track="ai")
        with patch.object(drama_scorer, "score_story") as mocked:
            drama_scorer.score_all_pending()
        # Only the 1 drama-track story from setUp should have been attempted.
        self.assertEqual(mocked.call_count, 1)


if __name__ == "__main__":
    unittest.main()
