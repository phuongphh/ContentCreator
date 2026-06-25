"""Tests for video.composer_moviepy (P2 / V2.2).

The MoviePy runtime is not exercised (optional, heavy dependency); we test the
pure spec builder and the missing-dependency contract.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from video.composer_moviepy import (
    build_subtitle_specs, compose, plan_multi_bg_segments,
)


class TestPlanMultiBgSegments(unittest.TestCase):
    def test_cycles_clip_indices(self):
        # 30s / 6s = 5 slots across 2 clips -> 0,1,0,1,0
        self.assertEqual(
            plan_multi_bg_segments(2, 30.0, 6), [0, 1, 0, 1, 0]
        )

    def test_ceil_division(self):
        # ceil(20/6) == 4 slots
        self.assertEqual(len(plan_multi_bg_segments(3, 20.0, 6)), 4)

    def test_matches_ffmpeg_segment_count(self):
        # Same cadence the FFmpeg engine uses (build_multi_bg_command).
        from video.video_composer import build_multi_bg_command
        cmd = build_multi_bg_command(["a", "b"], "o.mp4", 1920, 1080, 30.0, 6)
        fc = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn(f"concat=n={len(plan_multi_bg_segments(2, 30.0, 6))}", fc)

    def test_empty_for_zero_clips_or_duration(self):
        self.assertEqual(plan_multi_bg_segments(0, 30.0, 6), [])
        self.assertEqual(plan_multi_bg_segments(2, 0, 6), [])


class TestBuildSubtitleSpecs(unittest.TestCase):
    def test_one_spec_per_entry(self):
        entries = [(0.0, 2.0, "a"), (2.0, 5.0, "b")]
        specs = build_subtitle_specs(entries, 1920, 1080, 48)
        self.assertEqual(len(specs), 2)

    def test_timing_and_duration(self):
        specs = build_subtitle_specs([(1.0, 3.5, "hi")], 1920, 1080, 48)
        self.assertEqual(specs[0]["start"], 1.0)
        self.assertAlmostEqual(specs[0]["duration"], 2.5)
        self.assertEqual(specs[0]["text"], "hi")

    def test_bottom_centered_position(self):
        specs = build_subtitle_specs([(0.0, 1.0, "x")], 1080, 1920, 64)
        pos = specs[0]["position"]
        self.assertEqual(pos[0], "center")
        self.assertEqual(pos[1], int(1920 * 0.78))

    def test_width_constrained(self):
        specs = build_subtitle_specs([(0.0, 1.0, "x")], 1920, 1080, 48)
        self.assertEqual(specs[0]["size"][0], int(1920 * 0.8))

    def test_empty_entries(self):
        self.assertEqual(build_subtitle_specs([], 1920, 1080, 48), [])


class TestComposeMissingDependency(unittest.TestCase):
    @unittest.skipIf(
        __import__("importlib").util.find_spec("moviepy") is not None,
        "moviepy is installed; skipping missing-dependency test",
    )
    def test_returns_none_without_moviepy(self):
        result = compose("a.mp3", "s.srt", "out.mp4", video_type="long")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
