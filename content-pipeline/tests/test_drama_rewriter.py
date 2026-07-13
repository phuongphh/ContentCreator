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


def _text_message(text: str, stop_reason: str = "end_turn"):
    """A fake anthropic message carrying arbitrary reply text."""
    msg = MagicMock()
    msg.usage = None
    msg.stop_reason = stop_reason
    msg.content = [MagicMock(text=text)]
    return msg


def _fake_message(json_dict: dict, stop_reason: str = "end_turn"):
    return _text_message(json.dumps(json_dict, ensure_ascii=False), stop_reason)


def _mock_anthropic(json_dict: dict):
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _fake_message(json_dict)
    return patch.object(drama_rewriter.anthropic, "Anthropic", return_value=fake_client)


def _mock_anthropic_sequence(*messages):
    """Patch so consecutive .messages.create(...) calls return these in order."""
    fake_client = MagicMock()
    fake_client.messages.create.side_effect = list(messages)
    return patch.object(drama_rewriter.anthropic, "Anthropic", return_value=fake_client)


class _FakeAPIError(drama_rewriter.anthropic.APIError):
    """anthropic.APIError instance without the version-specific __init__ args."""

    def __init__(self):  # noqa: D107 — bypass parent's request/body requirement
        pass


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


class TestExtractJson(unittest.TestCase):
    def test_raw_json(self):
        self.assertEqual(drama_rewriter._extract_json('{"a": 1}'), {"a": 1})

    def test_fenced_json(self):
        text = '```json\n{"a": 1}\n```'
        self.assertEqual(drama_rewriter._extract_json(text), {"a": 1})

    def test_json_with_trailing_prose(self):
        text = '{"a": 1}\n\nHy vọng bản dịch này phù hợp!'
        self.assertEqual(drama_rewriter._extract_json(text), {"a": 1})

    def test_json_with_leading_prose(self):
        text = 'Đây là kết quả:\n{"a": 1}'
        self.assertEqual(drama_rewriter._extract_json(text), {"a": 1})

    def test_truncated_json_raises(self):
        # Cut off before the closing brace — the issue #82 shape.
        with self.assertRaises(drama_rewriter._RewriteParseError):
            drama_rewriter._extract_json('{"title": "abc", "script": "một câu chuyện')

    def test_no_json_raises(self):
        with self.assertRaises(drama_rewriter._RewriteParseError):
            drama_rewriter._extract_json("Xin lỗi, tôi không thể giúp việc này.")


class TestReplyText(unittest.TestCase):
    def test_first_text_block(self):
        self.assertEqual(drama_rewriter._reply_text(_text_message("hello")), "hello")

    def test_empty_content_returns_empty_string(self):
        msg = MagicMock()
        msg.content = []
        self.assertEqual(drama_rewriter._reply_text(msg), "")

    def test_skips_leading_non_text_block(self):
        msg = MagicMock()
        non_text = MagicMock(spec=[])  # no .text attribute
        msg.content = [non_text, MagicMock(text="real")]
        self.assertEqual(drama_rewriter._reply_text(msg), "real")


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

    def test_non_json_reply_flags_needs_review_and_saves_raw(self):
        # A model that replies with prose (e.g. a refusal) rather than JSON is
        # flagged for human review with the raw reply saved — not silently
        # retried forever (issue #82: persistent parse failure must be visible).
        refusal = "Xin lỗi, tôi không thể viết lại câu chuyện này."
        with _mock_anthropic_sequence(*([_text_message(refusal)] * 3)), \
             patch("notifier.telegram_bot.send_alert") as alert:
            result = drama_rewriter.rewrite_story(self.story_id)
        self.assertIsNone(result)
        story = stories.get_story(self.story_id)
        self.assertEqual(story["status"], "needs_review")
        saved = json.loads(story["rewritten_content"])
        self.assertIn(refusal, saved["_raw_reply"])
        self.assertIn("not valid JSON", saved["_rewrite_error"])
        alert.assert_called_once()

    def test_truncated_json_escalates_max_tokens_then_succeeds(self):
        # First reply is cut off mid-JSON (stop_reason='max_tokens'); the retry
        # must raise the ceiling and then succeed instead of truncating again.
        truncated = _text_message('{"title": "abc", "script": "', stop_reason="max_tokens")
        good = _fake_message(_good_rewrite())
        with _mock_anthropic_sequence(truncated, good) as p:
            fake_client = p.return_value
            result = drama_rewriter.rewrite_story(self.story_id)
        self.assertIsNotNone(result)
        story = stories.get_story(self.story_id)
        self.assertEqual(story["status"], "approved")
        # Second call must use a larger max_tokens than the first.
        calls = fake_client.messages.create.call_args_list
        self.assertGreater(calls[1].kwargs["max_tokens"], calls[0].kwargs["max_tokens"])

    def test_persistent_truncation_flags_needs_review(self):
        truncated = _text_message('{"title": "abc"', stop_reason="max_tokens")
        with _mock_anthropic_sequence(*([truncated] * 3)), \
             patch("notifier.telegram_bot.send_alert") as alert:
            result = drama_rewriter.rewrite_story(self.story_id)
        self.assertIsNone(result)
        story = stories.get_story(self.story_id)
        self.assertEqual(story["status"], "needs_review")
        saved = json.loads(story["rewritten_content"])
        self.assertEqual(saved["_stop_reason"], "max_tokens")
        alert.assert_called_once()

    def test_api_error_leaves_story_untouched_for_retry(self):
        # The model was never reached — leave the story 'pending' so a later
        # run retries it (transient), rather than flagging needs_review.
        fake_client = MagicMock()
        fake_client.messages.create.side_effect = _FakeAPIError()
        with patch.object(drama_rewriter.anthropic, "Anthropic", return_value=fake_client):
            result = drama_rewriter.rewrite_story(self.story_id)
        self.assertIsNone(result)
        story = stories.get_story(self.story_id)
        self.assertEqual(story["status"], "pending")
        self.assertIsNone(story["rewritten_content"])

    def test_uses_configured_max_tokens(self):
        with _mock_anthropic(_good_rewrite()) as p:
            fake_client = p.return_value
            drama_rewriter.rewrite_story(self.story_id)
        first_call = fake_client.messages.create.call_args_list[0]
        self.assertEqual(
            first_call.kwargs["max_tokens"], drama_rewriter.config.DRAMA_REWRITER_MAX_TOKENS
        )

    def test_sends_assistant_prefill_to_force_json(self):
        # Issue #84: the request must seed the assistant turn with "{" so the
        # model can only continue as a JSON object (no prose preamble).
        with _mock_anthropic(_good_rewrite()) as p:
            fake_client = p.return_value
            drama_rewriter.rewrite_story(self.story_id)
        msgs = fake_client.messages.create.call_args_list[0].kwargs["messages"]
        self.assertEqual(msgs[0]["role"], "user")
        self.assertEqual(msgs[-1]["role"], "assistant")
        self.assertEqual(msgs[-1]["content"], drama_rewriter._JSON_PREFILL)

    def test_reply_without_leading_brace_is_reconstructed(self):
        # A real prefilled call returns content WITHOUT the leading "{" (the API
        # does not echo the prefill). The code must prepend it before parsing.
        body = json.dumps(_good_rewrite(), ensure_ascii=False)
        self.assertTrue(body.startswith("{"))
        prefilled_reply = body[1:]  # what the model actually returns after "{"
        with _mock_anthropic_sequence(_text_message(prefilled_reply)), \
             patch("notifier.telegram_bot.send_alert"):
            result = drama_rewriter.rewrite_story(self.story_id)
        self.assertIsNotNone(result)
        story = stories.get_story(self.story_id)
        self.assertEqual(story["status"], "approved")

    def test_prose_preamble_before_brace_still_parses(self):
        # Defense in depth: even if a reply slips a preamble in before the JSON
        # (prefill not honored for some reason), _extract_json recovers the
        # object rather than failing outright — the reconstructed "{" + text
        # keeps the object balanced.
        prefilled_reply = "\n  " + json.dumps(_good_rewrite(), ensure_ascii=False)[1:]
        with _mock_anthropic_sequence(_text_message(prefilled_reply)), \
             patch("notifier.telegram_bot.send_alert"):
            result = drama_rewriter.rewrite_story(self.story_id)
        self.assertIsNotNone(result)
        self.assertEqual(stories.get_story(self.story_id)["status"], "approved")


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

    def test_skips_needs_review_story(self):
        # Issue #84 #2: a story already flagged 'needs_review' (a prior
        # unparseable rewrite) must NOT be picked up again — otherwise the same
        # poison story re-burns Sonnet tokens on every run. It is excluded on
        # two counts: status != 'pending' AND rewritten_content is set.
        stories.update_status(
            self.story_id, "needs_review",
            rewritten_content='{"_rewrite_error": "prior failure"}',
        )
        with patch.object(drama_rewriter, "rewrite_story") as mocked:
            count = drama_rewriter.rewrite_all_scored()
        mocked.assert_not_called()
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
