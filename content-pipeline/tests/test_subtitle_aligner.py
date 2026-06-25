"""Tests for video.subtitle_aligner (Phase 1 / V1.1 Whisper alignment).

The Whisper model itself is never loaded here; we test the pure mapping logic
and the graceful-fallback contract (missing lib / no words -> None).
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import video.subtitle_aligner as aligner
from video.subtitle_aligner import (
    _map_segments_to_words, _spoken_word_count, align,
)


class TestMapSegmentsToWords(unittest.TestCase):
    def test_uses_word_timing_for_segments(self):
        segments = ["xin chào", "các bạn nhé"]
        words = [
            ("xin", 0.0, 0.4), ("chào", 0.4, 1.0),
            ("các", 1.2, 1.5), ("bạn", 1.5, 1.9), ("nhé", 1.9, 2.4),
        ]
        out = _map_segments_to_words(segments, words)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0], (0.0, 1.0, "xin chào"))
        self.assertEqual(out[1][0], 1.2)
        self.assertAlmostEqual(out[1][1], 2.4)
        self.assertEqual(out[1][2], "các bạn nhé")

    def test_keeps_script_text_not_transcript(self):
        # Whisper word text differs from script; output must use script text.
        segments = ["Doanh thu 100 tỷ"]
        words = [("doanh", 0.0, 0.3), ("thu", 0.3, 0.6),
                 ("một", 0.6, 0.8), ("trăm", 0.8, 1.1)]
        out = _map_segments_to_words(segments, words)
        self.assertEqual(out[0][2], "Doanh thu 100 tỷ")  # original, with "100"

    def test_non_overlapping_monotonic(self):
        segments = ["a b", "c d"]
        words = [("a", 0.0, 1.0), ("b", 0.5, 2.0), ("c", 1.0, 1.2), ("d", 1.2, 3.0)]
        out = _map_segments_to_words(segments, words)
        for i in range(1, len(out)):
            self.assertGreaterEqual(out[i][0], out[i - 1][1])
        for start, end, _ in out:
            self.assertLessEqual(start, end)

    def test_stops_when_words_exhausted(self):
        segments = ["a", "b", "c"]
        words = [("a", 0.0, 0.5)]
        out = _map_segments_to_words(segments, words)
        self.assertEqual(len(out), 1)

    def test_respects_explicit_counts(self):
        # First segment should consume 3 spoken words, second consumes 1.
        segments = ["50%", "rồi"]
        words = [("năm", 0.0, 0.3), ("mươi", 0.3, 0.6),
                 ("phần_trăm", 0.6, 1.0), ("rồi", 1.0, 1.4)]
        out = _map_segments_to_words(segments, words, counts=[3, 1])
        self.assertEqual(out[0], (0.0, 1.0, "50%"))
        self.assertEqual(out[1], (1.0, 1.4, "rồi"))


class TestSpokenWordCount(unittest.TestCase):
    def test_expands_numbers(self):
        # "50%" is one display token but several spoken Vietnamese words.
        self.assertGreater(_spoken_word_count("50%"), 1)

    def test_plain_words_unchanged(self):
        self.assertEqual(_spoken_word_count("xin chào các bạn"), 4)


class TestAlignFallback(unittest.TestCase):
    def test_missing_audio_returns_none(self):
        self.assertIsNone(align("/no/such/audio.mp3", "xin chào"))

    def test_no_words_returns_none(self):
        with patch("os.path.exists", return_value=True), \
             patch.object(aligner, "_transcribe", return_value=None):
            self.assertIsNone(align("a.mp3", "xin chào các bạn"))

    def test_empty_script_returns_none(self):
        with patch("os.path.exists", return_value=True):
            self.assertIsNone(align("a.mp3", "   "))

    def test_success_path_builds_entries(self):
        words = [("xin", 0.0, 0.5), ("chào", 0.5, 1.0)]
        with patch("os.path.exists", return_value=True), \
             patch.object(aligner, "_transcribe", return_value=words):
            out = align("a.mp3", "xin chào")
        self.assertEqual(out, [(0.0, 1.0, "xin chào")])

    def test_partial_mapping_falls_back_to_none(self):
        # Two sentences need timing but Whisper only emits one word -> the
        # mapping covers a prefix; align() must return None so the caller uses
        # full-coverage word-count timing instead of subtitles that stop early.
        words = [("xin", 0.0, 0.5)]
        with patch("os.path.exists", return_value=True), \
             patch.object(aligner, "_transcribe", return_value=words):
            out = align("a.mp3", "Xin chào. Tạm biệt nhé.")
        self.assertIsNone(out)


class TestTranscribeImportGuard(unittest.TestCase):
    def test_missing_faster_whisper_returns_none(self):
        # Simulate the library being absent -> _get_model returns None.
        with patch.dict(sys.modules, {"faster_whisper": None}):
            aligner._MODEL = None
            aligner._MODEL_SIZE = None
            self.assertIsNone(aligner._get_model("base"))


if __name__ == "__main__":
    unittest.main()
