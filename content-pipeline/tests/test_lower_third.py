"""Tests for video/lower_third.py (Phase 4 — Drama Visual Assets)."""
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

from video.lower_third import render_lower_third


@unittest.skipUnless(HAS_PIL, "Pillow not installed")
class TestRenderLowerThird(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.out = os.path.join(self.tmp, "lower_third.png")

    def test_creates_file(self):
        result = render_lower_third("Mai", "Chị dâu", 1080, 1920, self.out)
        self.assertEqual(result, self.out)
        self.assertTrue(os.path.exists(self.out))

    def test_output_matches_requested_dimensions(self):
        render_lower_third("Mai", "Chị dâu", 1080, 1920, self.out)
        with Image.open(self.out) as img:
            self.assertEqual(img.size, (1080, 1920))

    def test_output_is_rgba_with_transparent_areas(self):
        render_lower_third("Mai", "Chị dâu", 1080, 1920, self.out)
        with Image.open(self.out) as img:
            self.assertEqual(img.mode, "RGBA")
            # Top-left corner (well above the bar) must be fully transparent.
            self.assertEqual(img.getpixel((0, 0))[3], 0)

    def test_bar_region_is_not_fully_transparent(self):
        render_lower_third("Mai", "Chị dâu", 1080, 1920, self.out)
        with Image.open(self.out) as img:
            bar_y = int(1920 * 0.72) + 5
            pixel = img.getpixel((5, bar_y))
            self.assertGreater(pixel[3], 0)

    def test_no_role_uses_name_only(self):
        result = render_lower_third("Mai", "", 1080, 1920, self.out)
        self.assertIsNotNone(result)

    def test_empty_name_returns_none(self):
        self.assertIsNone(render_lower_third("", "Chị dâu", 1080, 1920, self.out))
        self.assertFalse(os.path.exists(self.out))

    def test_whitespace_only_name_returns_none(self):
        self.assertIsNone(render_lower_third("   ", "Chị dâu", 1080, 1920, self.out))

    def test_creates_parent_directory(self):
        nested_out = os.path.join(self.tmp, "nested", "lt.png")
        result = render_lower_third("Mai", "Chị dâu", 1080, 1920, nested_out)
        self.assertEqual(result, nested_out)
        self.assertTrue(os.path.exists(nested_out))


if __name__ == "__main__":
    unittest.main()
