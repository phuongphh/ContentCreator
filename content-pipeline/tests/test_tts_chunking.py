"""Unit tests for TTS text splitting logic.

Tests _split_text() only — no actual TTS API calls are made.
"""
from __future__ import annotations

import sys
import os
import unittest

# Import _split_text directly without triggering config/dotenv loading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestSplitText(unittest.TestCase):
    """Test _split_text helper in tts_client."""

    def setUp(self):
        # Import here to avoid side effects at module load time
        from video.tts_client import _split_text, MAX_CHARS_PER_CHUNK
        self._split_text = _split_text
        self._max = MAX_CHARS_PER_CHUNK  # 700

    def test_short_text_no_split(self):
        text = "Xin chào! Đây là bài kiểm tra."
        result = self._split_text(text)
        self.assertEqual(result, [text])

    def test_exactly_at_limit_no_split(self):
        text = "A" * self._max
        result = self._split_text(text)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], text)

    def test_long_text_splits_into_multiple_chunks(self):
        # Two sentences each ~400 chars → must split into 2 chunks
        sentence1 = "Câu một " + "a" * 390 + "."
        sentence2 = "Câu hai " + "b" * 390 + "."
        text = f"{sentence1} {sentence2}"
        result = self._split_text(text)
        self.assertGreater(len(result), 1)

    def test_each_chunk_within_limit(self):
        # Build text with many short sentences that together exceed 700 chars
        sentences = [f"Câu số {i} kết thúc tại đây." for i in range(30)]
        text = " ".join(sentences)
        self.assertGreater(len(text), self._max)
        result = self._split_text(text)
        for chunk in result:
            self.assertLessEqual(len(chunk), self._max,
                                 f"Chunk too long ({len(chunk)} chars): {chunk[:50]}...")

    def test_no_content_lost(self):
        sentences = [f"Đây là câu số {i} trong bài kiểm tra." for i in range(25)]
        text = " ".join(sentences)
        result = self._split_text(text)
        # All words should be present — join and compare word sets
        original_words = set(text.split())
        result_words = set(" ".join(result).split())
        self.assertEqual(original_words, result_words)

    def test_single_oversized_sentence_splits_at_comma(self):
        # One sentence longer than 700 chars with commas
        clauses = [f"mệnh đề {i} trong câu rất dài này" for i in range(25)]
        long_sentence = ", ".join(clauses) + "."
        self.assertGreater(len(long_sentence), self._max)
        result = self._split_text(long_sentence)
        self.assertGreater(len(result), 1)
        for chunk in result:
            self.assertLessEqual(len(chunk), self._max)

    def test_empty_string_returns_list_with_empty(self):
        result = self._split_text("")
        # Should not crash; returns something usable
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)

    def test_vietnamese_sentence_endings(self):
        # Vietnamese sentences ending with ". " should split cleanly
        s1 = "Công cụ AI giúp bạn tiết kiệm thời gian. "
        s2 = "Hãy thử ngay hôm nay. "
        # Pad to force a split
        padding = "x" * 350
        text = (s1 + padding + ". ") + (s2 + padding + ".")
        result = self._split_text(text)
        self.assertGreater(len(result), 1)
        for chunk in result:
            self.assertLessEqual(len(chunk), self._max)

    def test_multiple_chunks_cover_all_content(self):
        # Ensure no text is dropped across many splits
        text = ("Câu ngắn đây. " * 100).strip()
        result = self._split_text(text)
        total_chars = sum(len(c) for c in result)
        # Allow for stripped whitespace at boundaries (≤ len(chunks) chars difference)
        self.assertAlmostEqual(total_chars, len(text), delta=len(result) * 2)


if __name__ == "__main__":
    unittest.main()
