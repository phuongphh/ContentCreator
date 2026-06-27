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
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

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


class TestErrorClassification(unittest.TestCase):
    """Issue #58: a stalled endpoint must NOT be retried; fast 5xx may be."""

    def test_timeout_is_not_retryable(self):
        self.assertFalse(tts._is_retryable(TimeoutError("timed out")))
        self.assertFalse(tts._is_retryable(URLError(TimeoutError("timed out"))))

    def test_ssl_error_is_not_retryable(self):
        self.assertFalse(tts._is_retryable(ssl.SSLError("handshake")))

    def test_transient_http_codes_are_retryable(self):
        for code in (429, 500, 502, 503, 504):
            err = HTTPError("http://x", code, "busy", {}, None)
            self.assertTrue(tts._is_retryable(err), code)

    def test_client_http_error_is_not_retryable(self):
        err = HTTPError("http://x", 400, "bad", {}, None)
        self.assertFalse(tts._is_retryable(err))

    def test_is_timeout_detects_wrapped_and_bare(self):
        self.assertTrue(tts._is_timeout(TimeoutError("t")))
        self.assertTrue(tts._is_timeout(URLError(TimeoutError("t"))))
        self.assertFalse(tts._is_timeout(URLError(ConnectionResetError())))
        self.assertFalse(tts._is_timeout(None))


class TestFailFastOnTimeout(unittest.TestCase):
    def test_timeout_is_not_retried(self):
        """A black-hole timeout fails after ONE attempt (no 3×400s stall)."""
        opener = MagicMock()
        opener.open.side_effect = TimeoutError("timed out")
        with patch.object(tts, "TTS_MAX_RETRIES", 3), \
             patch.object(tts, "_build_opener", return_value=opener), \
             patch.object(tts.time, "sleep") as sleep, \
             patch.multiple(tts.config, TTS_API_URL="https://tts.example/api",
                            TTS_API_KEY="", TTS_VOICE_ID="voice1",
                            TTS_VOICE_SPEED=1.0, TTS_ALLOW_INSECURE_SSL=False):
            with self.assertLogs(tts.logger, level="ERROR") as cm:
                result = tts._tts_single("xin chào", "/tmp/_tts_timeout.mp3")
        self.assertIsNone(result)
        self.assertEqual(opener.open.call_count, 1)   # no retry
        sleep.assert_not_called()                     # no backoff burned
        self.assertTrue(any("failing over" in m for m in cm.output))


class TestRetryTransientHttp(unittest.TestCase):
    def test_503_is_retried_with_backoff(self):
        """Fast 5xx still retries up to TTS_MAX_RETRIES (cheap, often recovers)."""
        opener = MagicMock()
        opener.open.side_effect = HTTPError("https://tts.example/api", 503,
                                            "busy", {}, None)
        with patch.object(tts, "TTS_MAX_RETRIES", 3), \
             patch.object(tts, "_build_opener", return_value=opener), \
             patch.object(tts.time, "sleep") as sleep, \
             patch.multiple(tts.config, TTS_API_URL="https://tts.example/api",
                            TTS_API_KEY="", TTS_VOICE_ID="voice1",
                            TTS_VOICE_SPEED=1.0, TTS_ALLOW_INSECURE_SSL=False):
            result = tts._tts_single("xin chào", "/tmp/_tts_503.mp3")
        self.assertIsNone(result)
        self.assertEqual(opener.open.call_count, 3)   # all attempts used
        self.assertEqual(sleep.call_count, 2)         # backoff between attempts


if __name__ == "__main__":
    unittest.main()
