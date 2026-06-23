"""Tests for video.video_composer pure helpers (SRT parsing, time, wrapping).

The ffmpeg/Pillow-dependent paths are not exercised here (they require external
binaries / system fonts); these tests cover the deterministic logic that drives
subtitle placement and timing.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from video.video_composer import _parse_srt, _srt_time_to_sec

try:
    from PIL import ImageFont  # noqa: F401
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False


SAMPLE_SRT = """1
00:00:00,000 --> 00:00:02,500
Xin chào các bạn

2
00:00:02,500 --> 00:00:05,000
Hôm nay có tin AI mới
"""

# Uses '.' as the millisecond separator (some tools emit this variant).
DOT_SRT = """1
00:00:01.000 --> 00:00:03.250
Dòng phụ đề
"""


class TestSrtTimeToSec(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(_srt_time_to_sec("00:00:00,000"), 0.0)

    def test_comma_millis(self):
        self.assertAlmostEqual(_srt_time_to_sec("00:00:02,500"), 2.5)

    def test_dot_millis(self):
        self.assertAlmostEqual(_srt_time_to_sec("00:00:03.250"), 3.25)

    def test_minutes_and_hours(self):
        self.assertAlmostEqual(_srt_time_to_sec("01:02:03,000"), 3723.0)


class TestParseSrt(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _write(self, content, name="test.srt"):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_missing_file_returns_empty(self):
        self.assertEqual(_parse_srt(os.path.join(self.tmp, "nope.srt")), [])

    def test_parses_entries(self):
        entries = _parse_srt(self._write(SAMPLE_SRT))
        self.assertEqual(len(entries), 2)
        start, end, text = entries[0]
        self.assertEqual(start, 0.0)
        self.assertAlmostEqual(end, 2.5)
        self.assertEqual(text, "Xin chào các bạn")

    def test_entries_are_time_ordered(self):
        entries = _parse_srt(self._write(SAMPLE_SRT))
        starts = [e[0] for e in entries]
        self.assertEqual(starts, sorted(starts))

    def test_dot_separator_supported(self):
        entries = _parse_srt(self._write(DOT_SRT))
        self.assertEqual(len(entries), 1)
        self.assertAlmostEqual(entries[0][0], 1.0)
        self.assertAlmostEqual(entries[0][1], 3.25)

    def test_multiline_text_joined(self):
        srt = "1\n00:00:00,000 --> 00:00:02,000\nDòng một\nDòng hai\n"
        entries = _parse_srt(self._write(srt))
        self.assertEqual(entries[0][2], "Dòng một Dòng hai")

    def test_malformed_block_skipped(self):
        srt = "1\nthis is not a timecode\nsome text\n"
        self.assertEqual(_parse_srt(self._write(srt)), [])

    def test_roundtrip_with_generated_srt(self):
        # Cross-check against the real subtitle generator output.
        from video.subtitle_generator import generate_srt
        out = os.path.join(self.tmp, "gen.srt")
        generate_srt("Câu một. Câu hai dài hơn một chút.", 12.0, out)
        entries = _parse_srt(out)
        self.assertTrue(entries)
        self.assertAlmostEqual(entries[-1][1], 12.0, delta=0.1)


@unittest.skipUnless(_HAS_PIL, "Pillow not installed")
class TestWrapText(unittest.TestCase):
    def test_wraps_long_text_into_multiple_lines(self):
        from video.video_composer import _wrap_text
        font = ImageFont.load_default()
        text = " ".join(["word"] * 60)
        lines = _wrap_text(text, font, max_width=120)
        self.assertGreater(len(lines), 1)
        # All original words preserved across the wrapped lines.
        self.assertEqual(" ".join(lines).split(), text.split())

    def test_empty_text(self):
        from video.video_composer import _wrap_text
        font = ImageFont.load_default()
        self.assertEqual(_wrap_text("", font, max_width=100), [""])


if __name__ == "__main__":
    unittest.main()
