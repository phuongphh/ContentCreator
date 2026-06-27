"""Tests for video.tts_client SSL hardening (Phase 0 / V0.1).

These tests inspect the SSL context the opener is built with; they do not make
any network calls.
"""
from __future__ import annotations

import json
import logging
import os
import ssl
import sys
import tempfile
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


class _FakeResp:
    """Minimal context-manager HTTP response."""

    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeOpener:
    """Dispatch opener.open() by URL substring to queued bytes/exceptions.

    routes maps a URL substring to a list of items; each call to open() consumes
    the next item (the last item repeats once the queue is down to one), so a
    route can model a status that progresses processing -> done.
    """

    def __init__(self, routes):
        self.routes = {k: list(v) for k, v in routes.items()}
        self.calls = []  # full URLs opened, in order

    def open(self, req, timeout=None):
        url = req.full_url
        self.calls.append(url)
        for sub, seq in self.routes.items():
            if sub in url:
                item = seq.pop(0) if len(seq) > 1 else seq[0]
                if isinstance(item, Exception):
                    raise item
                return _FakeResp(item)
        raise AssertionError(f"no fake route for {url}")

    def count(self, sub):
        return sum(1 for u in self.calls if sub in u)


def _json(obj):
    return json.dumps(obj).encode("utf-8")


class _AsyncFlowBase(unittest.TestCase):
    """Common config patching for the async job-flow tests."""

    def setUp(self):
        self.cfg = patch.multiple(
            tts.config,
            TTS_API_URL="http://tts.nuitruc.ai/api/tts",
            TTS_API_KEY="",
            TTS_VOICE_ID="voice8",
            TTS_VOICE_SPEED=1.0,
            TTS_ALLOW_INSECURE_SSL=False,
        )
        self.cfg.start()
        self.addCleanup(self.cfg.stop)
        # Never sleep for real in poll/backoff loops.
        self.sleep = patch.object(tts.time, "sleep").start()
        self.addCleanup(patch.stopall)
        self.out = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
        self.addCleanup(lambda: os.path.exists(self.out) and os.remove(self.out))

    def _run(self, opener):
        with patch.object(tts, "_build_opener", return_value=opener):
            return tts._tts_single("xin chào thế giới", self.out)


class TestAsyncHappyPath(_AsyncFlowBase):
    def test_submit_poll_result(self):
        opener = _FakeOpener({
            "/submit": [_json({"job_id": "abc"})],
            "/status/abc": [_json({"status": "processing"}),
                            _json({"status": "done"})],
            "/result/abc": [b"WAVDATA"],
        })
        result = self._run(opener)
        self.assertEqual(result, self.out)
        with open(self.out, "rb") as f:
            self.assertEqual(f.read(), b"WAVDATA")
        # /result fetched exactly once (one-shot job).
        self.assertEqual(opener.count("/result/abc"), 1)
        # Polled status at least twice (processing then done).
        self.assertGreaterEqual(opener.count("/status/abc"), 2)


class TestAsyncFailureModes(_AsyncFlowBase):
    def test_status_error_fails_over_without_fetching_result(self):
        opener = _FakeOpener({
            "/submit": [_json({"job_id": "abc"})],
            "/status/abc": [_json({"status": "error"})],
            "/result/abc": [b"SHOULD-NOT-FETCH"],
        })
        result = self._run(opener)
        self.assertIsNone(result)
        self.assertEqual(opener.count("/result/abc"), 0)

    def test_poll_timeout_fails_over(self):
        with patch.object(tts, "TTS_POLL_TIMEOUT", -1):  # deadline already past
            opener = _FakeOpener({
                "/submit": [_json({"job_id": "abc"})],
                "/status/abc": [_json({"status": "processing"})],
                "/result/abc": [b"NOPE"],
            })
            result = self._run(opener)
        self.assertIsNone(result)
        self.assertEqual(opener.count("/result/abc"), 0)

    def test_status_max_failures_fails_over(self):
        with patch.object(tts, "TTS_POLL_MAX_FAILURES", 2), \
             patch.object(tts, "TTS_MAX_RETRIES", 1):
            opener = _FakeOpener({
                "/submit": [_json({"job_id": "abc"})],
                "/status/abc": [OSError("boom")],  # every poll fails
                "/result/abc": [b"NOPE"],
            })
            result = self._run(opener)
        self.assertIsNone(result)
        self.assertEqual(opener.count("/status/abc"), 2)  # capped
        self.assertEqual(opener.count("/result/abc"), 0)

    def test_submit_without_job_id_fails(self):
        opener = _FakeOpener({"/submit": [_json({"detail": "nope"})]})
        result = self._run(opener)
        self.assertIsNone(result)
        self.assertEqual(opener.count("/status"), 0)


class TestEndpointBuilder(unittest.TestCase):
    def test_builds_suburls(self):
        with patch.object(tts.config, "TTS_API_URL", "http://x/api/tts"):
            self.assertEqual(tts._endpoint("submit"), "http://x/api/tts/submit")
            self.assertEqual(tts._endpoint("status/7"), "http://x/api/tts/status/7")

    def test_tolerates_trailing_slash(self):
        with patch.object(tts.config, "TTS_API_URL", "http://x/api/tts/"):
            self.assertEqual(tts._endpoint("result/7"), "http://x/api/tts/result/7")


if __name__ == "__main__":
    unittest.main()
