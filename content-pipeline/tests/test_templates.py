"""Tests for video/templates/ (Phase 4 EPIC #4.2 — scene template registry)."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from video.templates import load_template, DRAMA_SHORTS_TEMPLATE, AI_SHORTS_TEMPLATE

_REQUIRED_SCENE_KEYS = {"type", "duration", "background"}


class TestLoadTemplate(unittest.TestCase):
    def test_loads_drama_shorts(self):
        self.assertIs(load_template("drama", "shorts"), DRAMA_SHORTS_TEMPLATE)

    def test_loads_ai_shorts(self):
        self.assertIs(load_template("ai", "shorts"), AI_SHORTS_TEMPLATE)

    def test_defaults_to_shorts_format(self):
        self.assertIs(load_template("drama"), DRAMA_SHORTS_TEMPLATE)

    def test_unknown_track_raises(self):
        with self.assertRaises(ValueError):
            load_template("nonexistent_track", "shorts")

    def test_unknown_format_raises(self):
        with self.assertRaises(ValueError):
            load_template("drama", "long")


class TestTemplateStructure(unittest.TestCase):
    """Structural invariants both templates must satisfy — a template that
    fails these would break video/drama_composer.py's assumptions."""

    def _check_template(self, template: dict):
        self.assertIn("format", template)
        self.assertIn("duration_target", template)
        self.assertIn("scenes", template)
        self.assertGreater(len(template["scenes"]), 0)
        for scene in template["scenes"]:
            missing = _REQUIRED_SCENE_KEYS - scene.keys()
            self.assertFalse(missing, f"scene {scene} missing keys: {missing}")
            self.assertGreater(scene["duration"], 0)

    def test_drama_shorts_structure(self):
        self._check_template(DRAMA_SHORTS_TEMPLATE)

    def test_ai_shorts_structure(self):
        self._check_template(AI_SHORTS_TEMPLATE)

    def test_drama_scene_durations_sum_close_to_target(self):
        total = sum(s["duration"] for s in DRAMA_SHORTS_TEMPLATE["scenes"])
        self.assertEqual(total, DRAMA_SHORTS_TEMPLATE["duration_target"])

    def test_ai_scene_durations_sum_close_to_target(self):
        total = sum(s["duration"] for s in AI_SHORTS_TEMPLATE["scenes"])
        self.assertEqual(total, AI_SHORTS_TEMPLATE["duration_target"])

    def test_drama_has_exactly_one_commentary_scene(self):
        commentary_scenes = [s for s in DRAMA_SHORTS_TEMPLATE["scenes"] if s.get("commentary")]
        self.assertEqual(len(commentary_scenes), 1)

    def test_drama_has_exactly_one_lower_third_scene(self):
        lt_scenes = [s for s in DRAMA_SHORTS_TEMPLATE["scenes"] if s.get("lower_third")]
        self.assertEqual(len(lt_scenes), 1)
        self.assertEqual(lt_scenes[0]["type"], "escalation")


if __name__ == "__main__":
    unittest.main()
