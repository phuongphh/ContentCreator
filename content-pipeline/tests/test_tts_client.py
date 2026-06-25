"""Tests for video.tts_client SSL hardening (Phase 0 / V0.1).

These tests inspect the SSL context the opener is built with; they do not make
any network calls.
"""
from __future__ import annotations

import logging
import os
import ssl
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import video.tts_client as tts


def _extract_ssl_context(opener):
    """Pull the SSLContext out of an opener's HTTPSHandler."""
    for handler in opener.handlers:
        ctx = getattr(handler, "_context", None)
        if isinstance(ctx, ssl.SSLContext):
            return ctx
    raise AssertionError("no HTTPSHandler with an SSL context found")


class TestSecureByDefault(unittest.TestCase):
    def test_default_verifies_certificate(self):
        ctx = _extract_ssl_context(tts._build_opener(insecure=False))
        self.assertTrue(ctx.check_hostname)
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)

    def test_reads_config_flag_when_not_overridden(self):
        # Default config flag is False -> verifying context.
        with patch.object(tts.config, "TTS_ALLOW_INSECURE_SSL", False):
            ctx = _extract_ssl_context(tts._build_opener())
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)


class TestInsecureOptIn(unittest.TestCase):
    def test_insecure_disables_verification(self):
        ctx = _extract_ssl_context(tts._build_opener(insecure=True))
        self.assertFalse(ctx.check_hostname)
        self.assertEqual(ctx.verify_mode, ssl.CERT_NONE)

    def test_insecure_logs_warning(self):
        with self.assertLogs(tts.logger, level="WARNING") as cm:
            tts._build_opener(insecure=True)
        self.assertTrue(any("DISABLED" in m for m in cm.output))

    def test_config_flag_enables_insecure(self):
        with patch.object(tts.config, "TTS_ALLOW_INSECURE_SSL", True):
            ctx = _extract_ssl_context(tts._build_opener())
        self.assertEqual(ctx.verify_mode, ssl.CERT_NONE)


class TestNoSecretLogging(unittest.TestCase):
    def test_token_not_logged_on_failure(self):
        """A failed TTS call must not leak the Authorization token into logs."""
        with patch.object(tts.config, "TTS_API_URL", "https://tts.example/api"), \
             patch.object(tts.config, "TTS_API_KEY", "super-secret-token"), \
             patch.object(tts.config, "TTS_VOICE_ID", "voice1"), \
             patch.object(tts.config, "TTS_VOICE_SPEED", 1.0), \
             patch.object(tts.config, "TTS_ALLOW_INSECURE_SSL", False), \
             patch.object(tts, "TTS_MAX_RETRIES", 1):
            # Force the opener to fail fast.
            with patch.object(tts, "_build_opener") as mock_opener:
                mock_opener.return_value.open.side_effect = OSError("boom")
                with self.assertLogs(tts.logger, level="INFO") as cm:
                    result = tts._tts_single("xin chào", "/tmp/_tts_test_out.mp3")
        self.assertIsNone(result)
        joined = "\n".join(cm.output)
        self.assertNotIn("super-secret-token", joined)
        self.assertNotIn("Bearer super-secret-token", joined)


if __name__ == "__main__":
    unittest.main()
