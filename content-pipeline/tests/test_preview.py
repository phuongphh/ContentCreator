"""Tests for video/preview.py (Phase 5 — Telegram preview compression)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import video.preview as preview


class TestBuildPreviewCommand(unittest.TestCase):
    def test_command_shape(self):
        cmd = preview.build_preview_command("in.mp4", "out.mp4", 720, 28, "96k")
        self.assertEqual(cmd[0], "ffmpeg")
        self.assertIn("in.mp4", cmd)
        self.assertEqual(cmd[-1], "out.mp4")
        self.assertIn("-crf", cmd)
        self.assertEqual(cmd[cmd.index("-crf") + 1], "28")
        # scale theo chiều ngắn để xử lý được cả video dọc lẫn ngang
        scale = cmd[cmd.index("-vf") + 1]
        self.assertIn("720", scale)
        self.assertIn("gt(iw,ih)", scale)


class TestCompressForPreview(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.src = os.path.join(self.tmp, "video.mp4")
        with open(self.src, "wb") as f:
            f.write(b"x" * 1000)

    def test_small_file_returned_as_is(self):
        result = preview.compress_for_preview(self.src, max_bytes=10_000)
        self.assertEqual(result, self.src)

    def test_missing_file_returns_none(self):
        self.assertIsNone(preview.compress_for_preview(
            os.path.join(self.tmp, "nope.mp4")))

    def test_oversized_compresses_once_when_first_pass_fits(self):
        def fake_ffmpeg(cmd, out):
            with open(out, "wb") as f:
                f.write(b"y" * 100)  # nhỏ hơn max
            return out

        with patch.object(preview, "_run_ffmpeg", side_effect=fake_ffmpeg) as run:
            result = preview.compress_for_preview(self.src, max_bytes=500)
        self.assertEqual(run.call_count, 1)
        self.assertTrue(result.endswith("_preview.mp4"))

    def test_second_pass_when_still_too_big(self):
        sizes = iter([800, 100])  # pass 1 vẫn quá 500 bytes, pass 2 lọt

        def fake_ffmpeg(cmd, out):
            with open(out, "wb") as f:
                f.write(b"y" * next(sizes))
            return out

        with patch.object(preview, "_run_ffmpeg", side_effect=fake_ffmpeg) as run:
            result = preview.compress_for_preview(self.src, max_bytes=500)
        self.assertEqual(run.call_count, 2)
        self.assertIsNotNone(result)

    def test_returns_none_when_nothing_fits(self):
        def fake_ffmpeg(cmd, out):
            with open(out, "wb") as f:
                f.write(b"y" * 900)
            return out

        with patch.object(preview, "_run_ffmpeg", side_effect=fake_ffmpeg):
            self.assertIsNone(preview.compress_for_preview(self.src, max_bytes=500))

    def test_ffmpeg_failure_returns_none(self):
        with patch.object(preview, "_run_ffmpeg", return_value=None):
            self.assertIsNone(preview.compress_for_preview(self.src, max_bytes=500))


if __name__ == "__main__":
    unittest.main()
