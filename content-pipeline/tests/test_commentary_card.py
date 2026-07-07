"""Tests for video/commentary_card.py (Phase 4 — Drama Video Production)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from PIL import Image, ImageFont
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

from video.commentary_card import (
    render_commentary_card,
    _fit_commentary_text,
    CARD_FONTSIZE_RATIO,
)


def _resizable_default_font(size):
    """A `_load_font` stand-in that actually honors `size` (unlike the
    no-args ``ImageFont.load_default()`` fallback `_load_font` uses when no
    system TTF is found — see video_composer._FALLBACK_FONTS, all macOS
    paths). Keeps the shrink-to-fit tests deterministic regardless of which
    fonts happen to be installed on the machine running the suite."""
    return ImageFont.load_default(size=size)


@unittest.skipUnless(HAS_PIL, "Pillow not installed")
class TestRenderCommentaryCard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.out = os.path.join(self.tmp, "commentary.png")

    def test_creates_file(self):
        result = render_commentary_card("Góc nhìn của tôi là...", 1080, 1920, self.out)
        self.assertEqual(result, self.out)
        self.assertTrue(os.path.exists(self.out))

    def test_output_matches_requested_dimensions(self):
        render_commentary_card("Góc nhìn của tôi", 1080, 1920, self.out)
        with Image.open(self.out) as img:
            self.assertEqual(img.size, (1080, 1920))

    def test_background_is_tinted_not_transparent(self):
        render_commentary_card("Góc nhìn của tôi", 1080, 1920, self.out)
        with Image.open(self.out) as img:
            corner = img.getpixel((0, 0))
            self.assertEqual(corner[3], 235)  # near-opaque, matches CARD_BG_COLOR alpha

    def test_long_text_wraps_into_multiple_lines(self):
        long_text = " ".join(["từ"] * 100)
        result = render_commentary_card(long_text, 1080, 1920, self.out)
        self.assertIsNotNone(result)  # must not crash on long wrapped text

    def test_long_commentary_shrinks_font_to_fit_and_stays_on_canvas(self):
        # Phase 3's drama_rewriter validates vn_commentary at >=200 words —
        # at the default font size that wraps into more lines than a 1920px
        # frame is tall, which (before the fix) pushed the earliest lines to
        # a negative y (drawn off-canvas, silently lost).
        long_commentary = " ".join(["đây", "là", "một", "câu", "bình", "luận"] * 40)
        with patch("video.commentary_card._load_font", side_effect=_resizable_default_font):
            font, lines, line_h, line_spacing = _fit_commentary_text(long_commentary, 1080, 1920)
        total_h = len(lines) * line_h + max(0, len(lines) - 1) * line_spacing
        top = max(0, (1920 - total_h) // 2)
        self.assertGreaterEqual(top, 0)
        default_fontsize = max(16, int(1920 * CARD_FONTSIZE_RATIO))
        self.assertLess(font.size, default_fontsize)

    def test_short_text_keeps_default_fontsize(self):
        with patch("video.commentary_card._load_font", side_effect=_resizable_default_font):
            font, _, _, _ = _fit_commentary_text("Góc nhìn của tôi", 1080, 1920)
        default_fontsize = max(16, int(1920 * CARD_FONTSIZE_RATIO))
        self.assertEqual(font.size, default_fontsize)

    def test_render_long_commentary_end_to_end_with_real_load_font(self):
        # Integration check with the real (possibly no-op-on-this-box)
        # _load_font: must still not crash, and must not raise even when the
        # fallback bitmap font can't actually shrink.
        long_commentary = " ".join(["đây", "là", "một", "câu", "bình", "luận"] * 40)
        result = render_commentary_card(long_commentary, 1080, 1920, self.out)
        self.assertIsNotNone(result)

    def test_empty_text_returns_none(self):
        self.assertIsNone(render_commentary_card("", 1080, 1920, self.out))
        self.assertFalse(os.path.exists(self.out))

    def test_whitespace_only_text_returns_none(self):
        self.assertIsNone(render_commentary_card("   ", 1080, 1920, self.out))

    def test_creates_parent_directory(self):
        nested_out = os.path.join(self.tmp, "nested", "cc.png")
        result = render_commentary_card("Test", 1080, 1920, nested_out)
        self.assertEqual(result, nested_out)
        self.assertTrue(os.path.exists(nested_out))


if __name__ == "__main__":
    unittest.main()
