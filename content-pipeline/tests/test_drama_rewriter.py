"""Tests for processors/drama_rewriter.py (Phase 3 — Drama Generation Layer)."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import wave
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


class TestDialogueParsing(unittest.TestCase):
    def _turns(self, n):
        return [{"speaker": "A" if i % 2 == 0 else "B",
                 "display_name": "An" if i % 2 == 0 else "Bình",
                 "text": f"câu {i}"} for i in range(n)]

    def test_parse_none_for_legacy(self):
        self.assertIsNone(drama_rewriter.parse_dialogue(_good_rewrite()))

    def test_parse_drops_invalid_turns(self):
        r = {"dialogue": self._turns(2) + [{"speaker": "A", "text": ""}, "x"]}
        self.assertEqual(len(drama_rewriter.parse_dialogue(r)), 2)

    def test_conversation_needs_six_turns(self):
        self.assertEqual(drama_rewriter.validate_dialogue(self._turns(6)), [])
        self.assertTrue(drama_rewriter.validate_dialogue(self._turns(4)))

    def test_debate_needs_three_each(self):
        self.assertEqual(drama_rewriter.validate_dialogue(self._turns(6), "debate"), [])
        skewed = self._turns(6)
        skewed[1]["speaker"] = "A"
        self.assertTrue(drama_rewriter.validate_dialogue(skewed, "debate"))


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

    def _full_dialogue_object(self, fmt="conversation"):
        obj = _good_rewrite()
        obj["narrative_format"] = fmt
        obj["dialogue"] = [
            {"speaker": "A", "display_name": "Mai", "text": "Chào"},
            {"speaker": "B", "display_name": "Đức", "text": "Chào Mai"},
        ]
        return obj

    def test_prefill_body_with_nested_dialogue_restores_full_object(self):
        for fmt in ("conversation", "debate"):
            obj = self._full_dialogue_object(fmt)
            body = json.dumps(obj, ensure_ascii=False)[1:]
            parsed = drama_rewriter._extract_rewrite_json(body)
            self.assertEqual(parsed, obj)
            self.assertEqual(len(parsed["dialogue"]), 2)
            for key in drama_rewriter._REQUIRED_FIELDS:
                self.assertIn(key, parsed)

    def test_prefill_body_nested_dialogue_truncated_raises(self):
        body = json.dumps(self._full_dialogue_object(), ensure_ascii=False)[1:]
        cut = body[: body.index('"Chào Mai"') + 3]
        with self.assertRaises(drama_rewriter._RewriteParseError):
            drama_rewriter._extract_rewrite_json(cut)

    def test_prefill_body_with_leading_whitespace_nested_dialogue(self):
        obj = self._full_dialogue_object()
        body = "\n  " + json.dumps(obj, ensure_ascii=False)[1:]
        self.assertEqual(drama_rewriter._extract_rewrite_json(body), obj)

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


class TestNarrativeRotation(RewriterTestBase):
    def _new_story(self, number, track="drama", destination=None):
        sid = stories.insert_story("vn_original", f"rotation-{number}",
                                   f"Chuyện số {number}", track=track,
                                   metadata={"source_note": "keep me"})
        stories.update_status(sid, "pending", rubric_score=6,
                              destination=destination)
        return sid

    def _write(self, sid):
        fixture = _good_rewrite()
        fixture["dialogue"] = [
            {"speaker": "AB"[i % 2], "display_name": ("An", "Bình")[i % 2],
             "text": f"Câu {i}. " + " ".join(["từ"] * 48)}
            for i in range(6)
        ]
        with _mock_anthropic(fixture) as client:
            result = drama_rewriter.rewrite_story(sid)
        self.assertEqual(stories.get_story(sid)["status"], "approved")
        client.return_value.messages.create.assert_called_once()
        prompt = client.return_value.messages.create.call_args.kwargs["messages"][0]["content"]
        self.assertNotIn("{{", prompt)
        return result, prompt

    def test_twenty_new_scripts_across_separate_calls_follow_exact_cycle(self):
        expected = ["narrative"] * 4 + ["conversation"] + ["narrative"] * 4 + ["debate"]
        formats = []
        for number in range(1, 21):
            sid = self._new_story(number)
            result, prompt = self._write(sid)
            formats.append(result["narrative_format"])
            self.assertEqual(result["narrative_sequence"], number)
            saved = stories.get_story(sid)
            self.assertEqual(saved["metadata"]["source_note"], "keep me")
            self.assertEqual(json.loads(saved["rewritten_content"]), result)
            if number % 10 == 5:
                self.assertIn("CONVERSATION", prompt)
                self.assertIn("ít nhất 6 lượt nói", prompt)
                self.assertNotIn("Phong cách: kể chuyện ngôi thứ nhất", prompt)
            elif number % 10 == 0:
                self.assertIn("DEBATE", prompt)
                self.assertIn("phản biện trực tiếp", prompt)
                self.assertNotIn("Phong cách: kể chuyện ngôi thứ nhất", prompt)
        self.assertEqual(formats, expected * 2)

    def test_rewrite_existing_story_keeps_format_and_does_not_advance(self):
        for number in range(1, 6):
            sid = self._new_story(number)
            self._write(sid)
        self._write(self._new_story(6))
        before = stories.get_story(sid)
        with patch.object(drama_rewriter.anthropic, "Anthropic") as client:
            again = drama_rewriter.rewrite_story(sid)
            client.assert_not_called()
        self.assertEqual(stories.get_story(sid), before)
        self.assertEqual(again["narrative_format"], "conversation")
        self.assertEqual(again["narrative_sequence"], 5)
        following, _ = self._write(self._new_story(7))
        self.assertEqual(following["narrative_sequence"], 7)

    def test_api_and_parse_failures_do_not_consume_slot(self):
        for number in range(1, 5):
            self._write(self._new_story(number))
        sid = self._new_story(5)
        with _mock_anthropic_sequence(_FakeAPIError()):
            self.assertIsNone(drama_rewriter.rewrite_story(sid))
        with _mock_anthropic_sequence(*([_text_message("not json")] * 3)), \
             patch("notifier.telegram_bot.send_alert"):
            self.assertIsNone(drama_rewriter.rewrite_story(sid))
        result, _ = self._write(sid)
        self.assertEqual(result["narrative_sequence"], 5)
        self.assertEqual(result["narrative_format"], "conversation")

    def test_old_scripts_and_other_destinations_do_not_shift_rotation(self):
        stories.update_status(self.story_id, "produced",
                              rewritten_content=json.dumps(_good_rewrite()))
        for number, track, destination in [(1, "ai", None), (2, "drama", "tiktok_main")]:
            result, _ = self._write(self._new_story(number, track, destination))
            self.assertNotIn("narrative_sequence", result)
        result, _ = self._write(self._new_story(3, destination="drama_youtube"))
        self.assertEqual(result["narrative_sequence"], 1)

    def test_review_draft_counts_once_even_after_revalidation(self):
        draft = _good_rewrite()
        draft["hook"] = " ".join(["từ"] * 36)
        with _mock_anthropic(draft), \
             patch.object(drama_rewriter.config, "DRAMA_HOOK_HARD_MAX_WORDS", 35), \
             patch("notifier.telegram_bot.send_alert"):
            drama_rewriter.rewrite_story(self.story_id)
        self.assertEqual(stories.get_story(self.story_id)["status"], "needs_review")
        saved = stories.get_story(self.story_id)["rewritten_content"]
        with patch.object(drama_rewriter.config, "DRAMA_HOOK_HARD_MAX_WORDS", 40), \
             patch.object(drama_rewriter.anthropic, "Anthropic") as client:
            self.assertEqual(drama_rewriter.revalidate_needs_review(), 1)
            client.assert_not_called()
        reviewed = stories.get_story(self.story_id)
        self.assertEqual(reviewed["status"], "approved")
        self.assertEqual(reviewed["rewritten_content"], saved)
        result, _ = self._write(self._new_story(2))
        self.assertEqual(result["narrative_sequence"], 2)

    def test_fresh_process_resumes_saved_sequence(self):
        for number in range(1, 5):
            self._write(self._new_story(number))
        sid = self._new_story(5)
        code = (
            "import config, json, sys; config.DB_PATH = sys.argv[1]; "
            "from storage.stories import get_story; "
            "from storage.drama_narratives import select_narrative; "
            "print(json.dumps(select_narrative(get_story(int(sys.argv[2])))))"
        )
        child = subprocess.run(
            [sys.executable, "-c", code, self.dbpath, str(sid)],
            cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), "..")),
            capture_output=True, text=True, check=True, timeout=20,
        )
        self.assertEqual(json.loads(child.stdout),
                         {"sequence": 5, "format": "conversation"})

    def test_assigned_format_survives_failed_rewrite_and_other_new_stories(self):
        for number in range(1, 6):
            sid = self._new_story(number)
            self._write(sid)
        # A pre-v2 prose draft already owns the slot but can be rewritten.
        stories.update_status(sid, "pending", rewritten_content=json.dumps(_good_rewrite()))
        with _mock_anthropic_sequence(*([_text_message("invalid json")] * 3)), \
             patch("notifier.telegram_bot.send_alert"):
            self.assertIsNone(drama_rewriter.rewrite_story(sid))
        self._write(self._new_story(6))
        retried, _ = self._write(sid)
        self.assertEqual(retried["narrative_format"], "conversation")
        self.assertEqual(retried["narrative_sequence"], 5)
        following, _ = self._write(self._new_story(7))
        self.assertEqual(following["narrative_sequence"], 7)

    def test_stale_assignment_cannot_overwrite_another_stories_slot(self):
        from storage.drama_narratives import select_narrative, save_narrative
        stale = select_narrative(stories.get_story(self.story_id))
        self._write(self._new_story(1))
        with self.assertRaisesRegex(ValueError, "sequence changed"):
            save_narrative(self.story_id, "approved", _good_rewrite(), stale)
        self.assertIsNone(stories.get_story(self.story_id)["rewritten_content"])
        result, _ = self._write(self.story_id)
        self.assertEqual(result["narrative_sequence"], 2)

    def test_existing_assignment_cannot_be_replaced(self):
        from storage.drama_narratives import save_narrative
        self._write(self.story_id)
        before = stories.get_story(self.story_id)
        with self.assertRaisesRegex(ValueError, "Cannot change"):
            save_narrative(self.story_id, "approved", _good_rewrite(),
                           {"sequence": 5, "format": "conversation"})
        self.assertEqual(stories.get_story(self.story_id), before)

    def test_failed_save_rolls_back_counter_and_assignment(self):
        conn = db.get_connection()
        try:
            conn.execute("CREATE TRIGGER reject_draft BEFORE UPDATE ON stories "
                         "BEGIN SELECT RAISE(ABORT, 'test write failure'); END")
            conn.commit()
        finally:
            conn.close()
        with _mock_anthropic(_good_rewrite()), \
             self.assertRaisesRegex(sqlite3.IntegrityError, "test write failure"):
            drama_rewriter.rewrite_story(self.story_id)
        conn = db.get_connection()
        try:
            conn.execute("DROP TRIGGER reject_draft")
            conn.commit()
        finally:
            conn.close()
        result, _ = self._write(self._new_story(2))
        self.assertEqual(result["narrative_sequence"], 1)
        self.assertIsNone(stories.get_story(self.story_id)["rewritten_content"])

    def test_concurrent_rewrite_is_deferred_without_api_call(self):
        import fcntl
        with open(self.dbpath + ".drama-rewriter.lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with _mock_anthropic(_good_rewrite()) as client:
                self.assertIsNone(drama_rewriter.rewrite_story(self.story_id))
                client.assert_not_called()
        result, _ = self._write(self.story_id)
        self.assertEqual(result["narrative_sequence"], 1)

    def test_generic_prompt_version_does_not_override_dialogue_v2(self):
        for number in range(1, 5):
            self._write(self._new_story(number))
        with patch.object(drama_rewriter.config, "DRAMA_REWRITER_PROMPT_VERSION", "v1"):
            _, prompt = self._write(self._new_story(5))
        self.assertIn("CONVERSATION", prompt)


    def test_missing_dialogue_cannot_pass_zero_cost_revalidation(self):
        for number in range(1, 5):
            self._write(self._new_story(number))
        sid = self._new_story(5)
        with _mock_anthropic(_good_rewrite()), patch("notifier.telegram_bot.send_alert"):
            drama_rewriter.rewrite_story(sid)
        story = stories.get_story(sid)
        self.assertEqual(story["status"], "needs_review")
        self.assertIsNone(json.loads(story["rewritten_content"])["dialogue"])
        self.assertEqual(drama_rewriter.revalidate_needs_review(), 0)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg required")
    def test_persisted_dialogue_renders_with_two_voices_and_audio_timed_srt(self):
        from main_drama import _render_story
        from video.tts_client import get_audio_duration
        from video.drama_composer import build_drama_scene_reel
        from video.lower_third import render_lower_third
        for number in range(1, 6):
            sid = self._new_story(number)
            self._write(sid)

        def synth(text, path, *, voice_id, speed, allow_fallback):
            self.assertFalse(allow_fallback)
            with wave.open(path, "wb") as audio:
                audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
                sample = b"\x00\x01" if voice_id == "test-a" else b"\x00\x02"
                audio.writeframes(sample * 24000)
            return path

        def compose(audio_path, srt_path, output_path, **kwargs):
            template = {"scenes": [
                {"type": "escalation", "duration": 1, "background": "solid_blue"},
                {"type": "twist", "duration": 1, "background": "solid_blue"},
            ]}
            with tempfile.TemporaryDirectory() as tmp:
                reel = build_drama_scene_reel(
                    template, get_audio_duration(audio_path), 320, 568, tmp,
                    dialogue_turns=kwargs["dialogue_turns"],
                    dialogue_timeline=kwargs["dialogue_timeline"])
                self.assertIsNotNone(reel)
                shutil.copyfile(reel, output_path)
            return output_path

        with tempfile.TemporaryDirectory() as output, \
             patch.object(db.config, "VIDEO_OUTPUT_DIR", output), \
             patch.object(db.config, "DRAMA_DIALOGUE_GAP_SECONDS", 0.4), \
             patch.dict(db.config.DRAMA_CONVERSATION_VOICES, {"A": "test-a", "B": "test-b"}), \
             patch("video.tts_client.text_to_speech", side_effect=synth) as tts, \
             patch("video.drama_composer.compose_drama_video", side_effect=compose) as renderer, \
             patch("video.drama_composer.render_lower_third", wraps=render_lower_third) as labels, \
             patch("video.image_generator.generate_illustration", return_value=None), \
             patch("notifier.review_bot.auto_dispatch", return_value=True):
            video = db.get_video(_render_story(stories.get_story(sid)))
            self.assertEqual([c.kwargs["voice_id"] for c in tts.call_args_list],
                             ["test-a", "test-b"] * 3)
            self.assertEqual([c.args[0] for c in labels.call_args_list],
                             ["An", "Bình", "Bình", "An"])
            timeline = renderer.call_args.kwargs["dialogue_timeline"]
            self.assertAlmostEqual(timeline[-1][-1], 8.0)
            self.assertLess(abs(get_audio_duration(video["audio_path"]) - timeline[-1][-1]), 0.3)
            with open(video["subtitle_path"], encoding="utf-8") as srt:
                captions = srt.read()
            self.assertIn("00:00:01,400 -->", captions)
            self.assertIn("00:00:08,000", captions)
            self.assertIn("tôi chưa từng nghĩ", captions)
            self.assertIn("bình_luận", captions)
            self.assertEqual(stories.get_story(sid)["status"], "produced")


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
