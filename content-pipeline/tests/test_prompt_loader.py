"""Tests for processors/prompt_loader.py (Phase 3 — prompt versioning)."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import processors.prompt_loader as prompt_loader


class TestLoadPrompt(unittest.TestCase):
    def test_loads_scorer_v1(self):
        text = prompt_loader.load_prompt("drama", "scorer", version="v1")
        self.assertIn("HOOK_3S", text)
        self.assertIn("{{RAW_CONTENT}}", text)

    def test_loads_rewriter_v1(self):
        text = prompt_loader.load_prompt("drama", "rewriter", version="v1")
        self.assertIn("vn_commentary", text)

    def test_uses_config_prompt_version_by_default(self):
        from unittest.mock import patch
        with patch.object(prompt_loader.config, "PROMPT_VERSION", "v1"):
            text = prompt_loader.load_prompt("drama", "scorer")
        self.assertIn("HOOK_3S", text)

    def test_missing_prompt_raises(self):
        with self.assertRaises(FileNotFoundError):
            prompt_loader.load_prompt("drama", "does_not_exist", version="v1")

    def test_missing_version_raises(self):
        with self.assertRaises(FileNotFoundError):
            prompt_loader.load_prompt("drama", "scorer", version="v99")


class TestRender(unittest.TestCase):
    def test_replaces_single_placeholder(self):
        result = prompt_loader.render("Hello {{NAME}}!", NAME="World")
        self.assertEqual(result, "Hello World!")

    def test_replaces_multiple_placeholders(self):
        result = prompt_loader.render("{{A}} and {{B}}", A="x", B="y")
        self.assertEqual(result, "x and y")

    def test_leaves_literal_braces_untouched(self):
        # JSON-schema examples in prompts use single braces — render() must
        # not touch them, only {{DOUBLE_BRACE}} placeholders.
        template = 'Output: {"key": "value"} then {{FILLER}}'
        result = prompt_loader.render(template, FILLER="done")
        self.assertEqual(result, 'Output: {"key": "value"} then done')

    def test_unreferenced_kwarg_is_ignored(self):
        result = prompt_loader.render("no placeholders here", UNUSED="x")
        self.assertEqual(result, "no placeholders here")


if __name__ == "__main__":
    unittest.main()
