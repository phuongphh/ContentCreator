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


def _good_rewrite(word_count: int = 300, commentary_words: int = 100) -> dict:
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
        # Below the hard floor — a stub, still a blocking issue.
        issues = drama_rewriter.validate_rewrite(_good_rewrite(word_count=50))
        self.assertTrue(any("word count" in i for i in issues))

    def test_script_too_long_flagged(self):
        # Above the hard ceiling — runaway output, still blocked.
        issues = drama_rewriter.validate_rewrite(_good_rewrite(word_count=2000))
        self.assertTrue(any("word count" in i for i in issues))

    def test_script_below_ideal_but_above_hard_min_passes(self):
        # Issue #86: a complete script short of the ideal band but above the
        # hard floor must NOT be rejected — that was the whole bug.
        word_count = drama_rewriter.config.DRAMA_SCRIPT_SOFT_MIN_WORDS - 50
        self.assertEqual(
            drama_rewriter.validate_rewrite(_good_rewrite(word_count=word_count)), [])

    def test_script_at_hard_min_boundary_passes(self):
        self.assertEqual(
            drama_rewriter.validate_rewrite(
                _good_rewrite(word_count=drama_rewriter.config.DRAMA_SCRIPT_HARD_MIN_WORDS)
            ),
            [],
        )

    def test_script_just_below_hard_min_flagged(self):
        issues = drama_rewriter.validate_rewrite(
            _good_rewrite(word_count=drama_rewriter.config.DRAMA_SCRIPT_HARD_MIN_WORDS - 1)
        )
        self.assertTrue(any("word count" in i for i in issues))

    def test_script_above_ideal_but_below_hard_max_passes(self):
        # Symmetric to the min side: a complete script slightly over the 1200
        # ideal is accepted, not rejected like genuinely runaway output.
        self.assertEqual(
            drama_rewriter.validate_rewrite(
                _good_rewrite(word_count=drama_rewriter.config.DRAMA_SCRIPT_SOFT_MAX_WORDS + 100)
            ),
            [],
        )

    def test_script_just_above_hard_max_flagged(self):
        issues = drama_rewriter.validate_rewrite(
            _good_rewrite(word_count=drama_rewriter.config.DRAMA_SCRIPT_HARD_MAX_WORDS + 1)
        )
        self.assertTrue(any("word count" in i for i in issues))

    def test_short_vn_commentary_flagged(self):
        too_short = drama_rewriter.config.DRAMA_COMMENTARY_MIN_WORDS - 10
        issues = drama_rewriter.validate_rewrite(
            _good_rewrite(commentary_words=too_short))
        self.assertTrue(any("vn_commentary only" in i for i in issues))

    def test_overlong_hook_flagged(self):
        # Above the hard max (35) — a paragraph, not a 3s line: still blocking.
        result = _good_rewrite()
        result["hook"] = " ".join(["từ"] * 40)
        issues = drama_rewriter.validate_rewrite(result)
        self.assertTrue(any("hook has" in i for i in issues))

    def test_short_hook_not_flagged(self):
        result = _good_rewrite()
        result["hook"] = "Ba năm đi làm, tôi chưa từng nghĩ mình sẽ bị đối xử như vậy."
        self.assertEqual(drama_rewriter.validate_rewrite(result), [])

    def test_hook_one_word_over_ideal_passes(self):
        # Issue #99: story 574's hook ran 26 words — ONE over the old hard
        # limit of 25 — and was blocked to needs_review (a dead end for
        # stories). Slightly-long hooks must now approve with a soft note.
        result = _good_rewrite()
        result["hook"] = " ".join(["từ"] * 26)
        issues, notes = drama_rewriter.validate_rewrite_verdict(result)
        self.assertEqual(issues, [])
        self.assertTrue(any("hook has 26 words" in n for n in notes))

    def test_hook_at_hard_max_boundary_passes(self):
        result = _good_rewrite()
        result["hook"] = " ".join(
            ["từ"] * drama_rewriter.config.DRAMA_HOOK_HARD_MAX_WORDS
        )
        self.assertEqual(drama_rewriter.validate_rewrite(result), [])

    def test_hook_just_above_hard_max_flagged(self):
        result = _good_rewrite()
        result["hook"] = " ".join(
            ["từ"] * (drama_rewriter.config.DRAMA_HOOK_HARD_MAX_WORDS + 1)
        )
        issues = drama_rewriter.validate_rewrite(result)
        self.assertTrue(any("hook has" in i for i in issues))

    def test_ideal_hook_yields_no_soft_note(self):
        issues, notes = drama_rewriter.validate_rewrite_verdict(_good_rewrite())
        self.assertEqual(issues, [])
        self.assertEqual(notes, [])

    def test_verdict_collects_script_soft_note(self):
        # The two-tier verdict carries the issue #86 script note too, so the
        # approve path logs every soft signal from one call.
        word_count = drama_rewriter.config.DRAMA_SCRIPT_SOFT_MIN_WORDS - 50
        issues, notes = drama_rewriter.validate_rewrite_verdict(
            _good_rewrite(word_count=word_count)
        )
        self.assertEqual(issues, [])
        self.assertTrue(
            any(f"script word count {word_count}" in n for n in notes))

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

    def test_vn_reactions_optional_absence_ok(self):
        # vn_reactions is optional (only comment-carrying stories have it); a
        # perfectly good rewrite without it must still pass.
        self.assertEqual(drama_rewriter.validate_rewrite(_good_rewrite()), [])

    def test_vn_reactions_localization_enforced(self):
        # When present, vn_reactions obeys the same localization rules.
        result = _good_rewrite()
        result["vn_reactions"] = "Đám đông phán YTA, đúng kiểu thanksgiving drama"
        issues = drama_rewriter.validate_rewrite(result)
        self.assertTrue(any("thanksgiving" in i for i in issues))

    def test_clean_vn_reactions_passes(self):
        result = _good_rewrite()
        result["vn_reactions"] = "Cư dân mạng bình luận bên nhà trai quá đáng thật sự."
        self.assertEqual(drama_rewriter.validate_rewrite(result), [])

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


class TestExtractRewriteJson(unittest.TestCase):
    def test_honored_prefill_body_without_leading_brace(self):
        # The prefill "{" was consumed, so the reply is the body only.
        self.assertEqual(
            drama_rewriter._extract_rewrite_json('"a": 1, "b": 2}'), {"a": 1, "b": 2}
        )

    def test_full_object_as_is(self):
        self.assertEqual(drama_rewriter._extract_rewrite_json('{"a": 1}'), {"a": 1})

    def test_full_object_behind_prose_prefix(self):
        # Prefill ignored: a complete object behind prose must parse as-is,
        # NOT get a "{" prepended (which would corrupt it).
        self.assertEqual(
            drama_rewriter._extract_rewrite_json('Đây là:\n{"a": 1}'), {"a": 1}
        )

    def test_full_object_in_fence(self):
        self.assertEqual(
            drama_rewriter._extract_rewrite_json('```json\n{"a": 1}\n```'), {"a": 1}
        )

    def test_brace_led_but_unparseable_reraises(self):
        # Already brace-led and still broken (truncated) — do not double-prepend.
        with self.assertRaises(drama_rewriter._RewriteParseError):
            drama_rewriter._extract_rewrite_json('{"a": ')

    def test_bodyless_truncation_reraises(self):
        # Honored prefill + truncated body: neither the raw nor the "{"+raw
        # form yields a complete object.
        with self.assertRaises(drama_rewriter._RewriteParseError):
            drama_rewriter._extract_rewrite_json('"a": "unclosed')


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

    def test_below_ideal_script_still_approved(self):
        # Issue #86 end-to-end: a rewrite short of the ideal band but above the
        # hard floor must be APPROVED and produce a video, not sent to
        # needs_review. Regression for "pipeline rendered 0 videos".
        word_count = drama_rewriter.config.DRAMA_SCRIPT_SOFT_MIN_WORDS - 50
        with _mock_anthropic(_good_rewrite(word_count=word_count)), \
             patch("notifier.telegram_bot.send_alert") as alert:
            result = drama_rewriter.rewrite_story(self.story_id)
        self.assertIsNotNone(result)
        story = stories.get_story(self.story_id)
        self.assertEqual(story["status"], "approved")
        alert.assert_not_called()  # accepted scripts must not spam the alert

    def test_slightly_long_hook_still_approved(self):
        # Issue #99 end-to-end: a rewrite whose hook runs 26 words (one over
        # the ideal) must be APPROVED and produce a video — not sent to the
        # needs_review dead end, and not spam a Telegram alert.
        result = _good_rewrite()
        result["hook"] = " ".join(["từ"] * 26)
        with _mock_anthropic(result), \
             patch("notifier.telegram_bot.send_alert") as alert:
            out = drama_rewriter.rewrite_story(self.story_id)
        self.assertIsNotNone(out)
        story = stories.get_story(self.story_id)
        self.assertEqual(story["status"], "approved")
        alert.assert_not_called()

    def test_paragraph_hook_still_blocked(self):
        # Past the hard max the hook is a paragraph — structure went wrong,
        # so the rewrite still lands in needs_review with an alert.
        result = _good_rewrite()
        result["hook"] = " ".join(["từ"] * 50)
        with _mock_anthropic(result), \
             patch("notifier.telegram_bot.send_alert") as alert:
            drama_rewriter.rewrite_story(self.story_id)
        story = stories.get_story(self.story_id)
        self.assertEqual(story["status"], "needs_review")
        alert.assert_called_once()

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

    def test_full_object_behind_prose_prefix_still_parses(self):
        # Regression for the prefill-not-honored fallback (Codex P2 on PR #85):
        # if the model ignores the prefill and returns a COMPLETE object behind
        # a prose preamble, we must parse it as-is. Blindly prepending "{" would
        # corrupt it (synthetic brace makes raw_decode start on invalid input),
        # reintroducing the #84 failure in exactly this fallback path.
        reply = "Đây là kết quả:\n" + json.dumps(_good_rewrite(), ensure_ascii=False)
        with _mock_anthropic_sequence(_text_message(reply)), \
             patch("notifier.telegram_bot.send_alert"):
            result = drama_rewriter.rewrite_story(self.story_id)
        self.assertIsNotNone(result)
        self.assertEqual(stories.get_story(self.story_id)["status"], "approved")

    def test_full_object_in_json_fence_still_parses(self):
        # Same fallback, ```json-fenced form: a complete object wrapped in a
        # fence (prefill ignored) must still parse without the "{" prepend
        # breaking it.
        reply = "```json\n" + json.dumps(_good_rewrite(), ensure_ascii=False) + "\n```"
        with _mock_anthropic_sequence(_text_message(reply)), \
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


class TestRevalidateNeedsReview(RewriterTestBase):
    """Recovery path for stories stuck in 'needs_review' (issue #99)."""

    def _stick(self, result: dict) -> None:
        stories.update_status(
            self.story_id, "needs_review",
            rewritten_content=json.dumps(result, ensure_ascii=False),
        )

    def test_recovers_story_blocked_by_relaxed_rule(self):
        # The story 574 shape: a complete rewrite blocked only by the old
        # 25-word hook hard limit must be approved on re-validation — with the
        # saved rewrite untouched and zero AI calls.
        result = _good_rewrite()
        result["hook"] = " ".join(["từ"] * 26)
        self._stick(result)
        count = drama_rewriter.revalidate_needs_review()
        self.assertEqual(count, 1)
        story = stories.get_story(self.story_id)
        self.assertEqual(story["status"], "approved")
        self.assertEqual(json.loads(story["rewritten_content"]), result)

    def test_still_invalid_story_stays_needs_review(self):
        self._stick(_good_rewrite(word_count=50))  # genuinely broken stub
        count = drama_rewriter.revalidate_needs_review()
        self.assertEqual(count, 0)
        self.assertEqual(
            stories.get_story(self.story_id)["status"], "needs_review"
        )

    def test_error_envelope_skipped(self):
        # Unparseable-reply envelopes hold no rewrite to validate.
        stories.update_status(
            self.story_id, "needs_review",
            rewritten_content='{"_rewrite_error": "prior failure", "_raw_reply": "x"}',
        )
        count = drama_rewriter.revalidate_needs_review()
        self.assertEqual(count, 0)
        self.assertEqual(
            stories.get_story(self.story_id)["status"], "needs_review"
        )

    def test_malformed_content_skipped(self):
        stories.update_status(
            self.story_id, "needs_review", rewritten_content="not json at all"
        )
        count = drama_rewriter.revalidate_needs_review()
        self.assertEqual(count, 0)
        self.assertEqual(
            stories.get_story(self.story_id)["status"], "needs_review"
        )

    def test_sweeps_beyond_a_fixed_page_size(self):
        # Codex review (PR #100): with a fixed limit and newest-first ordering,
        # recoverable OLD stories would stay hidden behind newer ones once the
        # backlog exceeds the page. The default sweep must cover everything.
        recoverable = json.dumps(_good_rewrite(), ensure_ascii=False)
        ids = [self.story_id]
        for i in range(120):
            sid = stories.insert_story("reddit", f"stuck{i}", "raw", track="drama")
            ids.append(sid)
        for sid in ids:
            stories.update_status(sid, "needs_review", rewritten_content=recoverable)
        count = drama_rewriter.revalidate_needs_review()
        self.assertEqual(count, len(ids))
        # The OLDEST row (created first, sorted last) must also be recovered.
        self.assertEqual(stories.get_story(ids[0])["status"], "approved")


if __name__ == "__main__":
    unittest.main()
