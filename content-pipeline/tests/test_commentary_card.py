"""Tests for video/commentary_card.py (Phase 4 — Drama Video Production)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

from video.commentary_card import render_commentary_card


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
