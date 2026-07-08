"""Tests for publisher/youtube_uploader.py multi-channel additions (Phase 5)."""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import publisher.youtube_uploader as yu


class TestResolveTokenFile(unittest.TestCase):
    def test_reads_channel_env_from_config(self):
        with patch.object(yu.config, "YOUTUBE_DRAMA_TOKEN",
                          "publisher/.youtube_token_drama.json"):
            path = yu.resolve_token_file("drama_youtube")
        self.assertEqual(path, "publisher/.youtube_token_drama.json")

    def test_empty_env_falls_back_to_legacy_token(self):
        with patch.object(yu.config, "YOUTUBE_AI_TOKEN", ""), \
             patch.dict(os.environ, {"YOUTUBE_AI_TOKEN": ""}):
            path = yu.resolve_token_file("ai_youtube")
        self.assertEqual(path, yu.config.YOUTUBE_TOKEN_FILE)

    def test_unknown_channel_raises(self):
        with self.assertRaises(ValueError):
            yu.resolve_token_file("nonexistent")


class TestBuildVideoBody(unittest.TestCase):
    def _video(self, **overrides):
        video = {"id": 5, "video_type": "short", "track": "drama",
                 "youtube_title": "Mẹ chồng làm loạn",
                 "youtube_description": "desc",
                 "tiktok_hashtags": "#mechong #drama"}
        video.update(overrides)
        return video

    def test_drama_uses_entertainment_category(self):
        body = yu._build_video_body(self._video(), "drama_youtube")
        self.assertEqual(body["snippet"]["categoryId"], "24")

    def test_ai_keeps_existing_category_28(self):
        body = yu._build_video_body(self._video(track="ai"), "ai_youtube")
        self.assertEqual(body["snippet"]["categoryId"], "28")

    def test_short_gets_shorts_tag_in_title(self):
        body = yu._build_video_body(self._video(), "drama_youtube")
        self.assertIn("#Shorts", body["snippet"]["title"])

    def test_long_has_no_shorts_tag(self):
        body = yu._build_video_body(self._video(video_type="long"), "drama_youtube")
        self.assertNotIn("#Shorts", body["snippet"]["title"])

    def test_hashtags_merged_into_tags(self):
        body = yu._build_video_body(self._video(), "drama_youtube")
        self.assertIn("mechong", body["snippet"]["tags"])

    def test_privacy_from_config(self):
        with patch.object(yu.config, "YOUTUBE_PRIVACY", "unlisted"):
            body = yu._build_video_body(self._video(), "drama_youtube")
        self.assertEqual(body["status"]["privacyStatus"], "unlisted")

    def test_title_fallback_when_missing(self):
        body = yu._build_video_body(
            self._video(youtube_title="", tiktok_caption=""), "drama_youtube")
        self.assertTrue(body["snippet"]["title"])


class TestSplitHashtags(unittest.TestCase):
    def test_strips_hash_and_empties(self):
        self.assertEqual(yu._split_hashtags("#a #b c  # "), ["a", "b", "c"])
        self.assertEqual(yu._split_hashtags(""), [])
        self.assertEqual(yu._split_hashtags(None), [])


class TestExecuteResumable(unittest.TestCase):
    def test_retries_transient_then_succeeds(self):
        request = MagicMock()
        request.next_chunk.side_effect = [
            ConnectionError("reset"),
            TimeoutError("slow"),
            (None, {"id": "abc"}),
        ]
        with patch.object(yu.time, "sleep") as sleep:
            response = yu._execute_resumable(request)
        self.assertEqual(response, {"id": "abc"})
        self.assertEqual(sleep.call_count, 2)
        # exponential: 2s rồi 4s
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [2, 4])

    def test_non_transient_raises_immediately(self):
        request = MagicMock()
        request.next_chunk.side_effect = ValueError("bad metadata")
        with patch.object(yu.time, "sleep") as sleep:
            with self.assertRaises(ValueError):
                yu._execute_resumable(request)
        sleep.assert_not_called()

    def test_gives_up_after_max_retries(self):
        request = MagicMock()
        request.next_chunk.side_effect = ConnectionError("dead")
        with patch.object(yu.time, "sleep"):
            with self.assertRaises(ConnectionError):
                yu._execute_resumable(request)
        # 1 lần đầu + _MAX_CHUNK_RETRIES lần retry
        self.assertEqual(request.next_chunk.call_count, yu._MAX_CHUNK_RETRIES + 1)


class TestUploadToYoutubeGuards(unittest.TestCase):
    def test_missing_video_returns_none(self):
        with patch("storage.database.get_video", return_value=None):
            self.assertIsNone(yu.upload_to_youtube(999, "drama_youtube"))

    def test_missing_file_returns_none(self):
        video = {"id": 1, "video_path": "/nonexistent/v.mp4"}
        with patch("storage.database.get_video", return_value=video):
            self.assertIsNone(yu.upload_to_youtube(1, "drama_youtube"))

    def test_non_youtube_channel_returns_none(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
            video = {"id": 1, "video_path": f.name}
            with patch("storage.database.get_video", return_value=video):
                self.assertIsNone(yu.upload_to_youtube(1, "tiktok_main"))


if __name__ == "__main__":
    unittest.main()
