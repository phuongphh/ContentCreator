"""Tests for video.subtitle_generator — SRT generation from script + duration."""
from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from video.subtitle_generator import (
    generate_srt,
    _split_into_segments,
    _format_time,
    WORDS_PER_SEGMENT,
    build_wordcount_entries,
    write_entries_srt,
)


class TestFormatTime(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(_format_time(0), "00:00:00,000")

    def test_fractional_seconds(self):
        self.assertEqual(_format_time(1.5), "00:00:01,500")

    def test_minutes_and_seconds(self):
        self.assertEqual(_format_time(75), "00:01:15,000")

    def test_hours(self):
        self.assertEqual(_format_time(3661.250), "01:01:01,250")

    def test_millisecond_rounding_truncates(self):
        # 2.999 -> 2 sec, 999 ms
        self.assertEqual(_format_time(2.999), "00:00:02,999")


class TestSplitIntoSegments(unittest.TestCase):
    def test_short_sentence_kept_whole(self):
        segs = _split_into_segments("Hôm nay trời đẹp.")
        self.assertEqual(segs, ["Hôm nay trời đẹp."])

    def test_each_sentence_becomes_segment(self):
        text = "Câu một. Câu hai. Câu ba."
        segs = _split_into_segments(text)
        self.assertEqual(len(segs), 3)

    def test_long_sentence_is_split(self):
        # 24 words, > WORDS_PER_SEGMENT (10) -> must split into multiple segments
        sentence = " ".join(f"từ{i}" for i in range(24)) + "."
        segs = _split_into_segments(sentence)
        self.assertGreater(len(segs), 1)
        for seg in segs:
            self.assertLessEqual(len(seg.split()), WORDS_PER_SEGMENT)

    def test_split_prefers_comma_boundaries(self):
        sentence = ("một hai ba bốn năm sáu, "
                    "bảy tám chín mười mười_một mười_hai.")
        segs = _split_into_segments(sentence)
        self.assertGreaterEqual(len(segs), 2)

    def test_consecutive_duplicates_kept(self):
        # Subtitles must mirror the audio EXACTLY: TTS synthesizes from the
        # same text, so a repeated line IS spoken twice. The old behavior
        # (dropping the duplicate from subtitles only) re-distributed the
        # word-count timing and desynced every subtitle after the repeat —
        # the drama-track "title read twice, subtitles drift" bug. Repeats
        # are now removed at the source (main_drama.build_narration).
        text = "Cảm ơn các bạn. Cảm ơn các bạn."
        segs = _split_into_segments(text)
        self.assertEqual(segs, ["Cảm ơn các bạn.", "Cảm ơn các bạn."])

    def test_no_segments_for_whitespace(self):
        self.assertEqual(_split_into_segments("   "), [])


class TestGenerateSrt(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _out(self, name="sub.srt"):
        return os.path.join(self.tmp, name)

    def test_empty_text_returns_none(self):
        self.assertIsNone(generate_srt("", 10.0, self._out()))

    def test_zero_duration_returns_none(self):
        self.assertIsNone(generate_srt("Xin chào.", 0, self._out()))

    def test_negative_duration_returns_none(self):
        self.assertIsNone(generate_srt("Xin chào.", -5, self._out()))

    def test_creates_file_and_returns_path(self):
        out = self._out()
        result = generate_srt("Xin chào. Tạm biệt.", 10.0, out)
        self.assertEqual(result, out)
        self.assertTrue(os.path.exists(out))

    def test_creates_missing_parent_dir(self):
        out = os.path.join(self.tmp, "nested", "deep", "sub.srt")
        result = generate_srt("Xin chào thế giới.", 5.0, out)
        self.assertEqual(result, out)
        self.assertTrue(os.path.exists(out))

    def test_srt_structure_is_valid(self):
        out = self._out()
        generate_srt("Câu một. Câu hai. Câu ba.", 9.0, out)
        with open(out, encoding="utf-8") as f:
            content = f.read()
        # Each block: index, timecode, text
        blocks = re.split(r"\n\s*\n", content.strip())
        self.assertEqual(len(blocks), 3)
        first = blocks[0].splitlines()
        self.assertEqual(first[0], "1")
        self.assertRegex(
            first[1],
            r"\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}",
        )

    def test_timing_is_monotonic_and_covers_duration(self):
        out = self._out()
        duration = 30.0
        generate_srt(
            "Một hai ba. Bốn năm sáu. Bảy tám chín mười.", duration, out
        )
        with open(out, encoding="utf-8") as f:
            content = f.read()
        times = re.findall(
            r"(\d{2}):(\d{2}):(\d{2}),(\d{3}) --> (\d{2}):(\d{2}):(\d{2}),(\d{3})",
            content,
        )
        self.assertTrue(times)

        def to_sec(h, m, s, ms):
            return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000

        prev_end = 0.0
        last_end = 0.0
        for t in times:
            start = to_sec(*t[:4])
            end = to_sec(*t[4:])
            self.assertGreaterEqual(start, prev_end - 1e-6)  # no overlap
            self.assertGreaterEqual(end, start)
            prev_end = end
            last_end = end
        # Last subtitle should end at ~ the audio duration (proportional fill)
        self.assertAlmostEqual(last_end, duration, delta=0.1)

    def test_longer_segment_gets_more_time(self):
        # Two sentences with very different word counts -> proportional timing.
        out = self._out()
        short = "Chào."
        long = " ".join(["từ"] * 20) + "."
        generate_srt(f"{short} {long}", 21.0, out)
        with open(out, encoding="utf-8") as f:
            content = f.read()
        times = re.findall(
            r"(\d{2}):(\d{2}):(\d{2}),(\d{3}) --> (\d{2}):(\d{2}):(\d{2}),(\d{3})",
            content,
        )

        def dur(t):
            def to_sec(h, m, s, ms):
                return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
            return to_sec(*t[4:]) - to_sec(*t[:4])

        # First displayed segment ("Chào.") should be shorter than the last.
        self.assertLess(dur(times[0]), dur(times[-1]))


class TestBuildWordcountEntries(unittest.TestCase):
    def test_returns_tuples_covering_duration(self):
        entries = build_wordcount_entries("Câu một. Câu hai dài hơn.", 12.0)
        self.assertTrue(entries)
        self.assertAlmostEqual(entries[-1][1], 12.0, delta=0.05)
        self.assertEqual(entries[0][0], 0.0)

    def test_empty_text_returns_empty_list(self):
        self.assertEqual(build_wordcount_entries("   ", 10.0), [])


class TestWriteEntriesSrt(unittest.TestCase):
    """Shared SRT writer used by both word-count and Whisper paths (P1)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_writes_provided_entries(self):
        out = os.path.join(self.tmp, "w.srt")
        entries = [(0.0, 1.5, "Xin chào"), (1.5, 3.0, "Tạm biệt")]
        self.assertEqual(write_entries_srt(entries, out), out)
        with open(out, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("Xin chào", content)
        self.assertIn("00:00:01,500 --> 00:00:03,000", content)

    def test_empty_entries_returns_none(self):
        out = os.path.join(self.tmp, "empty.srt")
        self.assertIsNone(write_entries_srt([], out))

    def test_roundtrip_with_parser(self):
        from video.video_composer import _parse_srt
        out = os.path.join(self.tmp, "rt.srt")
        entries = [(0.0, 2.0, "một"), (2.0, 4.0, "hai")]
        write_entries_srt(entries, out)
        parsed = _parse_srt(out)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[1][2], "hai")


if __name__ == "__main__":
    unittest.main()
