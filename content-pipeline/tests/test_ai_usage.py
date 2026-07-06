"""Tests for processors/ai_usage.py (Phase 3 — token usage logging).

Uses a real logging handler at INFO level (not the test suite's default
WARNING root logger) — a %d-vs-%s formatting bug here would otherwise be
masked by INFO messages simply never being formatted/emitted.
"""
from __future__ import annotations

import logging
import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import processors.ai_usage as ai_usage


class TestLogTokenUsage(unittest.TestCase):
    def setUp(self):
        self.logger = logging.getLogger("processors.ai_usage")
        self._original_level = self.logger.level
        self.logger.setLevel(logging.INFO)

    def tearDown(self):
        self.logger.setLevel(self._original_level)

    def test_logs_with_real_int_usage_at_info_level(self):
        message = MagicMock()
        message.usage.input_tokens = 1234
        message.usage.output_tokens = 567
        with self.assertLogs("processors.ai_usage", level="INFO") as cm:
            ai_usage.log_token_usage("drama_scorer", 42, message)
        self.assertTrue(any("1234" in line and "567" in line for line in cm.output))

    def test_no_usage_attribute_does_not_raise(self):
        message = MagicMock()
        message.usage = None
        # Must not raise even with real INFO-level logging enabled.
        ai_usage.log_token_usage("drama_scorer", 42, message)

    def test_missing_token_fields_does_not_raise(self):
        message = MagicMock(spec=["usage"])
        message.usage = MagicMock(spec=[])  # no input_tokens/output_tokens attrs
        with self.assertLogs("processors.ai_usage", level="INFO"):
            ai_usage.log_token_usage("drama_scorer", 42, message)

    def test_unconfigured_mock_usage_does_not_raise(self):
        # A bare MagicMock() (as used by test fixtures elsewhere in this repo
        # for fake Anthropic responses) auto-vivifies `.usage.input_tokens`
        # as ANOTHER MagicMock, not an int — %d formatting would raise here;
        # %s must not.
        message = MagicMock()
        with self.assertLogs("processors.ai_usage", level="INFO"):
            ai_usage.log_token_usage("drama_scorer", 42, message)


if __name__ == "__main__":
    unittest.main()
