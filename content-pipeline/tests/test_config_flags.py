"""Tests for the video engine feature flags in config (Phase 0 / V0.3)."""
from __future__ import annotations

import importlib
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config


class TestFlagDefaults(unittest.TestCase):
    """With no env overrides, flags must equal the legacy behaviour."""

    def setUp(self):
        # Reload config with a clean env so defaults are deterministic.
        self._saved = {}
        for key in ("SUBTITLE_TIMING_MODE", "BACKGROUND_MODE", "TTS_PROVIDER",
                    "COMPOSER_ENGINE", "ENABLE_BGM", "TTS_ALLOW_INSECURE_SSL",
                    "BURN_SUBTITLES"):
            self._saved[key] = os.environ.pop(key, None)
        importlib.reload(config)

    def tearDown(self):
        for key, val in self._saved.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        importlib.reload(config)

    def test_subtitle_timing_default_is_wordcount(self):
        self.assertEqual(config.SUBTITLE_TIMING_MODE, "wordcount")

    def test_background_default_is_single(self):
        self.assertEqual(config.BACKGROUND_MODE, "single")

    def test_tts_provider_default_is_nuitruc(self):
        self.assertEqual(config.TTS_PROVIDER, "nuitruc")

    def test_composer_engine_default_is_ffmpeg(self):
        self.assertEqual(config.COMPOSER_ENGINE, "ffmpeg")

    def test_bgm_default_off(self):
        self.assertFalse(config.ENABLE_BGM)

    def test_insecure_ssl_default_off(self):
        self.assertFalse(config.TTS_ALLOW_INSECURE_SSL)

    def test_burn_subtitles_default_all(self):
        self.assertEqual(config.BURN_SUBTITLES, "all")


class TestShouldBurnSubtitles(unittest.TestCase):
    def _with_mode(self, mode):
        return patch.object(config, "BURN_SUBTITLES", mode)

    def test_all_burns_both(self):
        with self._with_mode("all"):
            self.assertTrue(config.should_burn_subtitles("short"))
            self.assertTrue(config.should_burn_subtitles("long"))

    def test_short_only(self):
        with self._with_mode("short_only"):
            self.assertTrue(config.should_burn_subtitles("short"))
            self.assertFalse(config.should_burn_subtitles("long"))

    def test_none_burns_nothing(self):
        with self._with_mode("none"):
            self.assertFalse(config.should_burn_subtitles("short"))
            self.assertFalse(config.should_burn_subtitles("long"))

    def test_unknown_falls_back_to_all(self):
        with self._with_mode("bogus"):
            self.assertTrue(config.should_burn_subtitles("long"))


class TestValidateFlags(unittest.TestCase):
    def test_valid_flags_no_issues(self):
        # Defaults are valid -> empty issue list.
        importlib.reload(config)
        self.assertEqual(config.validate_flags(), [])

    def test_invalid_flag_reported(self):
        importlib.reload(config)
        original = config.SUBTITLE_TIMING_MODE
        try:
            config.SUBTITLE_TIMING_MODE = "bogus"
            issues = config.validate_flags()
            self.assertTrue(any("SUBTITLE_TIMING_MODE" in i for i in issues))
        finally:
            config.SUBTITLE_TIMING_MODE = original

    def test_logger_warning_called(self):
        importlib.reload(config)
        import logging
        from unittest.mock import MagicMock
        original = config.COMPOSER_ENGINE
        try:
            config.COMPOSER_ENGINE = "nope"
            mock_logger = MagicMock(spec=logging.Logger)
            config.validate_flags(mock_logger)
            mock_logger.warning.assert_called()
        finally:
            config.COMPOSER_ENGINE = original


if __name__ == "__main__":
    unittest.main()
