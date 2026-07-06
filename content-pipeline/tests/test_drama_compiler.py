"""Tests for processors/drama_compiler.py (Phase 3 EPIC #3.3 — Drama Compiler)."""
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
import storage.compiled_videos as compiled_videos
import processors.drama_compiler as drama_compiler


def _fake_message(json_dict: dict):
    msg = MagicMock()
    msg.usage = None
    msg.content = [MagicMock(text=json.dumps(json_dict, ensure_ascii=False))]
    return msg


def _mock_anthropic(json_dict: dict):
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _fake_message(json_dict)
    return patch.object(drama_compiler.anthropic, "Anthropic", return_value=fake_client)


def _good_compiled_script(word_count: int = 1500) -> dict:
    return {
        "intro": "Hôm nay kể 3 câu chuyện về đồng nghiệp xấu tính.",
        "bridges": ["Tiếp theo là...", "Và cuối cùng..."],
        "outro": "Cảm ơn đã xem, đăng ký kênh nhé!",
        "chapters": ["00:00 Intro", "01:30 Story 1", "05:00 Story 2", "08:30 Story 3"],
        "full_script": " ".join(["từ"] * word_count),
    }


class DramaCompilerTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dbpath = os.path.join(self.tmp, "test.db")
        self._patch = patch.object(db.config, "DB_PATH", self.dbpath)
        self._patch.start()
        db.init_db()
        migrate.migrate_up()

    def tearDown(self):
        self._patch.stop()

    def _make_produced_story(self, source_id: str, title: str) -> int:
        story_id = stories.insert_story(
            "reddit", source_id, "raw content", track="drama", title=title,
        )
        stories.update_status(
            story_id, "produced",
            rewritten_content=json.dumps({
                "title": title,
                "script": f"Script cho {title}. " * 50,
            }, ensure_ascii=False),
        )
        return story_id


class TestDetectTheme(DramaCompilerTestBase):
    def test_too_few_stories_returns_none_without_calling_api(self):
        s1 = {"id": 1, "title": "A", "raw_content": "a"}
        s2 = {"id": 2, "title": "B", "raw_content": "b"}
        with patch.object(drama_compiler.anthropic, "Anthropic") as mocked_anthropic:
            result = drama_compiler.detect_theme([s1, s2])
        self.assertIsNone(result)
        mocked_anthropic.assert_not_called()

    def test_qualifying_theme_returned(self):
        candidates = [
            {"id": i, "title": f"Story {i}", "raw_content": "x"} for i in range(1, 4)
        ]
        json_dict = {"theme": "đồng nghiệp xấu tính", "story_ids": [1, 2, 3], "reason": "ok"}
        with _mock_anthropic(json_dict):
            result = drama_compiler.detect_theme(candidates)
        self.assertEqual(result["theme"], "đồng nghiệp xấu tính")
        self.assertEqual(result["story_ids"], [1, 2, 3])

    def test_below_threshold_theme_returns_none(self):
        candidates = [
            {"id": i, "title": f"Story {i}", "raw_content": "x"} for i in range(1, 4)
        ]
        json_dict = {"theme": "hiếm gặp", "story_ids": [1, 2], "reason": "not enough"}
        with _mock_anthropic(json_dict):
            result = drama_compiler.detect_theme(candidates)
        self.assertIsNone(result)

    def test_null_theme_returns_none(self):
        candidates = [
            {"id": i, "title": f"Story {i}", "raw_content": "x"} for i in range(1, 4)
        ]
        json_dict = {"theme": None, "story_ids": [], "reason": "no common theme"}
        with _mock_anthropic(json_dict):
            result = drama_compiler.detect_theme(candidates)
        self.assertIsNone(result)

    def test_malformed_response_returns_none(self):
        candidates = [
            {"id": i, "title": f"Story {i}", "raw_content": "x"} for i in range(1, 4)
        ]
        fake_client = MagicMock()
        not_json_message = MagicMock()
        not_json_message.usage = None
        not_json_message.content = [MagicMock(text="not json at all")]
        fake_client.messages.create.return_value = not_json_message
        with patch.object(drama_compiler.anthropic, "Anthropic", return_value=fake_client):
            result = drama_compiler.detect_theme(candidates)
        self.assertIsNone(result)


class TestCompileLongForm(DramaCompilerTestBase):
    def test_valid_script_returned(self):
        story_ids = [self._make_produced_story(f"s{i}", f"Story {i}") for i in range(3)]
        selected = [stories.get_story(sid) for sid in story_ids]
        with _mock_anthropic(_good_compiled_script()):
            result = drama_compiler.compile_long_form(selected, "test theme")
        self.assertIsNotNone(result)
        self.assertIn("full_script", result)

    def test_invalid_chapter_format_retries_then_fails(self):
        story_ids = [self._make_produced_story(f"t{i}", f"Story {i}") for i in range(3)]
        selected = [stories.get_story(sid) for sid in story_ids]
        bad = _good_compiled_script()
        bad["chapters"] = ["not a valid chapter marker"]
        with _mock_anthropic(bad):
            result = drama_compiler.compile_long_form(selected, "test theme")
        self.assertIsNone(result)

    def test_word_count_out_of_range_fails(self):
        story_ids = [self._make_produced_story(f"u{i}", f"Story {i}") for i in range(3)]
        selected = [stories.get_story(sid) for sid in story_ids]
        bad = _good_compiled_script(word_count=100)
        with _mock_anthropic(bad):
            result = drama_compiler.compile_long_form(selected, "test theme")
        self.assertIsNone(result)

    def test_missing_field_fails(self):
        story_ids = [self._make_produced_story(f"v{i}", f"Story {i}") for i in range(3)]
        selected = [stories.get_story(sid) for sid in story_ids]
        bad = _good_compiled_script()
        del bad["outro"]
        with _mock_anthropic(bad):
            result = drama_compiler.compile_long_form(selected, "test theme")
        self.assertIsNone(result)

    def test_caps_at_max_stories_per_compilation(self):
        story_ids = [self._make_produced_story(f"w{i}", f"Story {i}") for i in range(7)]
        selected = [stories.get_story(sid) for sid in story_ids]
        fake_client = MagicMock()
        fake_client.messages.create.return_value = _fake_message(_good_compiled_script())
        with patch.object(drama_compiler.anthropic, "Anthropic", return_value=fake_client):
            drama_compiler.compile_long_form(selected, "test theme")
        sent_prompt = fake_client.messages.create.call_args.kwargs["messages"][0]["content"]
        # Only MAX_STORIES_PER_COMPILATION (5) of the 7 should appear in the prompt.
        included = sum(1 for sid in story_ids if f"Story #{sid}" in sent_prompt)
        self.assertEqual(included, drama_compiler.MAX_STORIES_PER_COMPILATION)


class TestRunWeeklyCompilation(DramaCompilerTestBase):
    def test_no_produced_stories_returns_none(self):
        result = drama_compiler.run_weekly_compilation()
        self.assertIsNone(result)

    def test_full_flow_creates_compiled_video(self):
        story_ids = [self._make_produced_story(f"p{i}", f"Story {i}") for i in range(3)]
        theme_json = {"theme": "test theme", "story_ids": story_ids, "reason": "ok"}
        compile_json = _good_compiled_script()

        call_count = {"n": 0}

        def fake_create(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return _fake_message(theme_json)
            return _fake_message(compile_json)

        fake_client = MagicMock()
        fake_client.messages.create.side_effect = fake_create
        with patch.object(drama_compiler.anthropic, "Anthropic", return_value=fake_client):
            video_id = drama_compiler.run_weekly_compilation()

        self.assertIsNotNone(video_id)
        video = compiled_videos.get_compiled_video(video_id)
        self.assertEqual(video["theme"], "test theme")
        self.assertEqual(set(video["story_ids"]), set(story_ids))

    def test_theme_detection_failure_returns_none_without_compiling(self):
        self._make_produced_story("q0", "Story 0")
        self._make_produced_story("q1", "Story 1")
        self._make_produced_story("q2", "Story 2")
        json_dict = {"theme": None, "story_ids": [], "reason": "none found"}
        with _mock_anthropic(json_dict) as _, \
             patch.object(drama_compiler, "compile_long_form") as mocked_compile:
            result = drama_compiler.run_weekly_compilation()
        self.assertIsNone(result)
        mocked_compile.assert_not_called()


if __name__ == "__main__":
    unittest.main()
