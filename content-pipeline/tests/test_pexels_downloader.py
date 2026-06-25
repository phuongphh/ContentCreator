"""Tests for video.pexels_downloader pure logic (no network calls).

Network/download functions are not exercised; these tests cover cache-key
generation, best-file selection, and duration-matched background selection.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import video.pexels_downloader as pex
from video.pexels_downloader import (
    _cache_key,
    _cached_path,
    _find_best_file,
    _select_best_background,
    _any_cached,
    get_backgrounds,
)


class TestCacheKey(unittest.TestCase):
    def test_deterministic(self):
        self.assertEqual(
            _cache_key("blue gradient", "landscape"),
            _cache_key("blue gradient", "landscape"),
        )

    def test_orientation_changes_key(self):
        self.assertNotEqual(
            _cache_key("abstract", "landscape"),
            _cache_key("abstract", "portrait"),
        )

    def test_sanitizes_unsafe_chars(self):
        key = _cache_key("AI/Tech: 2024!", "landscape")
        # Only alphanumerics + underscore survive in the readable prefix.
        prefix = key.rsplit("_landscape_", 1)[0]
        self.assertTrue(all(c.isalnum() or c == "_" for c in prefix))

    def test_includes_orientation_and_hash(self):
        key = _cache_key("particles", "portrait")
        self.assertIn("_portrait_", key)

    def test_cached_path_has_mp4_extension(self):
        path = _cached_path("digital network", "landscape")
        self.assertTrue(path.endswith(".mp4"))


class TestFindBestFile(unittest.TestCase):
    def test_empty_returns_none(self):
        self.assertIsNone(_find_best_file([], "landscape"))

    def test_picks_landscape_candidate(self):
        files = [
            {"width": 1920, "height": 1080, "quality": "hd", "link": "a"},
            {"width": 640, "height": 360, "quality": "sd", "link": "b"},
        ]
        best = _find_best_file(files, "landscape")
        self.assertEqual(best["link"], "a")

    def test_prefers_hd_over_sd(self):
        files = [
            {"width": 1280, "height": 720, "quality": "sd", "link": "sd"},
            {"width": 1280, "height": 720, "quality": "hd", "link": "hd"},
        ]
        best = _find_best_file(files, "landscape")
        self.assertEqual(best["link"], "hd")

    def test_prefers_higher_resolution(self):
        files = [
            {"width": 1280, "height": 720, "quality": "hd", "link": "small"},
            {"width": 1920, "height": 1080, "quality": "hd", "link": "big"},
        ]
        best = _find_best_file(files, "landscape")
        self.assertEqual(best["link"], "big")

    def test_portrait_selection(self):
        files = [
            {"width": 1080, "height": 1920, "quality": "hd", "link": "p"},
            {"width": 1920, "height": 1080, "quality": "hd", "link": "l"},
        ]
        best = _find_best_file(files, "portrait")
        self.assertEqual(best["link"], "p")

    def test_portrait_falls_back_to_wide_files(self):
        # No true portrait files; fallback accepts wide files >= 1080 width.
        files = [{"width": 1920, "height": 1080, "quality": "hd", "link": "l"}]
        best = _find_best_file(files, "portrait")
        self.assertEqual(best["link"], "l")

    def test_rejects_too_small(self):
        files = [{"width": 320, "height": 240, "quality": "sd", "link": "tiny"}]
        self.assertIsNone(_find_best_file(files, "landscape"))


class TestSelectBestBackground(unittest.TestCase):
    def test_single_candidate_returned(self):
        self.assertEqual(_select_best_background(["only.mp4"], 30.0), "only.mp4")

    def test_unknown_duration_returns_a_candidate(self):
        paths = ["a.mp4", "b.mp4"]
        self.assertIn(_select_best_background(paths, 0), paths)

    @patch("video.pexels_downloader.get_video_duration")
    def test_picks_closest_duration_match(self, mock_dur):
        durations = {"short.mp4": 5.0, "match.mp4": 29.0, "long.mp4": 120.0}
        mock_dur.side_effect = lambda p: durations[p]
        chosen = _select_best_background(list(durations), audio_duration=30.0)
        self.assertEqual(chosen, "match.mp4")

    @patch("video.pexels_downloader.get_video_duration")
    def test_skips_unprobeable_files(self, mock_dur):
        # 0.0 means ffprobe failed; such files must not win selection.
        durations = {"bad.mp4": 0.0, "good.mp4": 28.0}
        mock_dur.side_effect = lambda p: durations[p]
        chosen = _select_best_background(list(durations), audio_duration=30.0)
        self.assertEqual(chosen, "good.mp4")


class TestAnyCached(unittest.TestCase):
    def test_no_cache_dir_returns_none(self):
        with patch.object(pex, "CACHE_DIR", "/nonexistent/path/xyz"):
            self.assertIsNone(_any_cached("landscape"))

    def test_matches_orientation_only(self):
        tmp = tempfile.mkdtemp()
        for name in ["a_landscape_111.mp4", "b_portrait_222.mp4",
                     "notes.txt"]:
            open(os.path.join(tmp, name), "w").close()
        with patch.object(pex, "CACHE_DIR", tmp):
            result = _any_cached("landscape", audio_duration=0)
        self.assertTrue(result.endswith("a_landscape_111.mp4"))


class TestGetBackgrounds(unittest.TestCase):
    """Multi-clip gathering (P1 / V1.2)."""

    def test_count_one_delegates_to_single(self):
        with patch.object(pex, "get_background", return_value="single.mp4") as m:
            result = get_backgrounds(orientation="landscape", count=1)
        self.assertEqual(result, ["single.mp4"])
        m.assert_called_once()

    def test_count_one_empty_when_no_single(self):
        with patch.object(pex, "get_background", return_value=None):
            self.assertEqual(get_backgrounds(count=1), [])

    def test_collects_cached_clips_up_to_count(self):
        tmp = tempfile.mkdtemp()
        # Pre-create cache files for two generic queries.
        from video.pexels_downloader import SEARCH_QUERIES
        paths = [pex._cached_path(q, "landscape") for q in SEARCH_QUERIES[:3]]
        with patch.object(pex, "CACHE_DIR", tmp):
            # _cached_path uses CACHE_DIR at call time
            paths = [pex._cached_path(q, "landscape") for q in SEARCH_QUERIES[:3]]
            for p in paths:
                open(p, "w").close()
            result = get_backgrounds(orientation="landscape", count=2)
        self.assertEqual(len(result), 2)

    def test_no_cache_no_apikey_returns_empty_or_fallback(self):
        tmp = tempfile.mkdtemp()
        with patch.object(pex, "CACHE_DIR", tmp), \
             patch.object(pex.config, "PEXELS_API_KEY", ""):
            result = get_backgrounds(orientation="landscape", count=3)
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
