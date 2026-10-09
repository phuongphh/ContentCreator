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
                    "TTS_API_VERSION", "COMPOSER_ENGINE", "ENABLE_BGM",
                    "TTS_ALLOW_INSECURE_SSL", "BURN_SUBTITLES"):
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

    def test_tts_api_version_default_is_v1_job_api(self):
        # Mặc định = job API 3 bước (submit/status/result) trên HTTPS.
        self.assertEqual(config.TTS_API_VERSION, "v1")
        self.assertEqual(config.TTS_API_URL, "https://tts.nuitruc.ai/api/tts")

    def test_tts_v2_settings_still_available(self):
        self.assertEqual(config.TTS_V2_API_URL,
                         "https://tts2.nuitruc.ai/v1/audio/speech")
        self.assertEqual(config.TTS_V2_MODEL, "nuitruc-tts-v2")

    def test_composer_engine_default_is_ffmpeg(self):
        self.assertEqual(config.COMPOSER_ENGINE, "ffmpeg")

    def test_bgm_default_off(self):
        self.assertFalse(config.ENABLE_BGM)

    def test_insecure_ssl_default_off(self):
        self.assertFalse(config.TTS_ALLOW_INSECURE_SSL)

    def test_burn_subtitles_default_all(self):
        self.assertEqual(config.BURN_SUBTITLES, "all")


class TestTtsProfileForTrack(unittest.TestCase):
    """config.tts_profile_for_track — single source of truth voice+speed/track."""

    def _reload_with(self, **env):
        """Reload config with *env* applied (và dọn lại sau test)."""
        saved = {k: os.environ.get(k) for k in env}
        os.environ.update({k: v for k, v in env.items()})

        def restore():
            for key, val in saved.items():
                if val is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = val
            importlib.reload(config)

        self.addCleanup(restore)
        importlib.reload(config)

    def test_defaults_ai_and_drama_on_v1(self):
        # v1 (job API) là mặc định: AI 1.5, Drama 1.0.
        self._reload_with(TTS_API_VERSION="v1", TTS_VOICE_SPEED_AI="",
                          TTS_VOICE_SPEED_DRAMA="")
        self.assertEqual(config.tts_profile_for_track("ai"), ("voice1", 1.5))
        self.assertEqual(config.tts_profile_for_track("drama"),
                         ("preset_my_duyen", 1.0))

    def test_defaults_ai_and_drama_on_v2(self):
        # v2 là engine khác: tốc độ mặc định 0.8 cho cả hai track.
        self._reload_with(TTS_API_VERSION="v2", TTS_VOICE_SPEED_AI="",
                          TTS_VOICE_SPEED_DRAMA="")
        self.assertEqual(config.tts_profile_for_track("ai"), ("voice1", 0.8))
        self.assertEqual(config.tts_profile_for_track("drama"),
                         ("preset_my_duyen", 0.8))

    def test_unknown_version_uses_v1_speeds_like_the_client_does(self):
        # _use_v2() định tuyến "v3" sang v1 → tốc độ mặc định cũng phải là của
        # v1, nếu không một lỗi chính tả sẽ đọc sai nhịp mà không báo gì.
        self._reload_with(TTS_API_VERSION="v3", TTS_VOICE_SPEED_AI="",
                          TTS_VOICE_SPEED_DRAMA="")
        self.assertEqual(config.tts_profile_for_track("ai")[1], 1.5)
        self.assertEqual(config.tts_profile_for_track("drama")[1], 1.0)

    def test_env_speed_wins_over_version_default(self):
        self._reload_with(TTS_API_VERSION="v1", TTS_VOICE_SPEED_AI="1.2")
        self.assertEqual(config.tts_profile_for_track("ai")[1], 1.2)

    def test_env_override(self):
        with patch.multiple(config, TTS_VOICE_ID_AI="custom", TTS_VOICE_SPEED_AI=2.0):
            self.assertEqual(config.tts_profile_for_track("ai"), ("custom", 2.0))

    def test_empty_voice_becomes_none(self):
        with patch.object(config, "TTS_VOICE_ID_DRAMA", ""):
            voice, speed = config.tts_profile_for_track("drama")
        self.assertIsNone(voice)

    def test_unknown_track_uses_global(self):
        with patch.multiple(config, TTS_VOICE_ID="g", TTS_VOICE_SPEED=1.1):
            self.assertEqual(config.tts_profile_for_track("zzz"), ("g", 1.1))


class TestEnvExampleRollback(unittest.TestCase):
    """`cp .env.example .env` + đổi TTS_API_VERSION phải ra ĐÚNG tốc độ của version.

    Nếu template điền sẵn số cho hai biến SPEED thì chúng ĐÈ mặc định theo
    version, và việc chuyển version bằng một biến sẽ sai tốc độ (vd. track AI
    kẹt ở 1.5 khi chuyển sang v2 thay vì 0.8).
    """

    def test_speed_overrides_left_blank_in_template(self):
        path = os.path.join(os.path.dirname(__file__), "..", ".env.example")
        with open(path, encoding="utf-8") as f:
            lines = [ln.strip() for ln in f]
        for key in ("TTS_VOICE_SPEED_AI", "TTS_VOICE_SPEED_DRAMA"):
            matching = [ln for ln in lines if ln.startswith(key + "=")]
            self.assertEqual(matching, [f"{key}="], key)


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

    def test_invalid_api_version_reported_with_v1_fallback_note(self):
        importlib.reload(config)
        original = config.TTS_API_VERSION
        try:
            config.TTS_API_VERSION = "v3"
            issues = config.validate_flags()
        finally:
            config.TTS_API_VERSION = original
        matching = [i for i in issues if "TTS_API_VERSION" in i]
        self.assertTrue(matching)
        self.assertIn("v1", matching[0])

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
