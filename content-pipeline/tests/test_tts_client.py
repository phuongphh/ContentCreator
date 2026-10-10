"""Tests for video.tts_client — SSL hardening (Phase 0 / V0.1), flow v1 (job
API) và flow v2 (endpoint /v1/audio/speech đồng bộ, mặc định).

Không test nào chạm mạng: mọi opener đều được mock.
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
        with patch.object(tts.config, "TTS_API_VERSION", "v1"), \
             patch.object(tts.config, "TTS_API_URL", "https://tts.example/api"), \
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
             patch.multiple(tts.config, TTS_API_VERSION="v1",
                            TTS_API_URL="https://tts.example/api",
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
             patch.multiple(tts.config, TTS_API_VERSION="v1",
                            TTS_API_URL="https://tts.example/api",
                            TTS_API_KEY="", TTS_VOICE_ID="voice1",
                            TTS_VOICE_SPEED=1.0, TTS_ALLOW_INSECURE_SSL=False):
            result = tts._tts_single("xin chào", "/tmp/_tts_503.mp3")
        self.assertIsNone(result)
        self.assertEqual(opener.open.call_count, 3)   # all attempts used
        self.assertEqual(sleep.call_count, 2)         # backoff between attempts


class _FakeResp:
    """Minimal context-manager HTTP response (headers optional)."""

    def __init__(self, body: bytes, headers: dict | None = None):
        self._body = body
        self.headers = _FakeHeaders(headers or {})

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeHeaders:
    """Vừa đủ giống email.message.Message để _open_with_retry đọc được."""

    def __init__(self, mapping: dict):
        self._mapping = dict(mapping)

    def items(self):
        return self._mapping.items()


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
            TTS_API_VERSION="v1",   # đây là bộ test của flow job cũ
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


class TestSubmitJobVoiceId(unittest.TestCase):
    """Phase 4 — per-call voice_id override on the /submit payload."""

    def setUp(self):
        self.cfg = patch.multiple(
            tts.config,
            TTS_API_VERSION="v1",
            TTS_API_URL="http://tts.nuitruc.ai/api/tts",
            TTS_API_KEY="",
            TTS_VOICE_ID="default_voice",
            TTS_VOICE_SPEED=1.0,
        )
        self.cfg.start()
        self.addCleanup(self.cfg.stop)

    def _capture_submit(self):
        captured = {}

        class FakeOpener:
            def open(self, req, timeout=None):
                captured["body"] = json.loads(req.data)
                return _FakeResp(_json({"job_id": "abc"}))

        return FakeOpener(), captured

    def test_custom_voice_id_included_in_payload(self):
        opener, captured = self._capture_submit()
        job_id = tts._submit_job(opener, "xin chào", voice_id="preset_custom")
        self.assertEqual(job_id, "abc")
        self.assertEqual(captured["body"]["voice_id"], "preset_custom")

    def test_no_override_falls_back_to_config(self):
        opener, captured = self._capture_submit()
        tts._submit_job(opener, "xin chào")
        self.assertEqual(captured["body"]["voice_id"], "default_voice")
        self.assertEqual(captured["body"]["speed"], 1.0)

    def test_custom_speed_included_in_payload(self):
        opener, captured = self._capture_submit()
        tts._submit_job(opener, "xin chào", voice_id="v1", speed=1.5)
        self.assertEqual(captured["body"]["speed"], 1.5)

    def test_speed_none_uses_config_default(self):
        opener, captured = self._capture_submit()
        tts._submit_job(opener, "xin chào", speed=None)
        self.assertEqual(captured["body"]["speed"], 1.0)


class TestSynthesizeForTrack(unittest.TestCase):
    """Per-track voice + speed — synthesize_for_track() resolves both."""

    def test_ai_track_uses_ai_voice_and_speed(self):
        with patch.multiple(tts.config, TTS_VOICE_ID_AI="voice1",
                            TTS_VOICE_SPEED_AI=1.5, TTS_VOICE_ID_DRAMA="preset_my_duyen",
                            TTS_VOICE_SPEED_DRAMA=1.0), \
             patch.object(tts, "text_to_speech", return_value="out.mp3") as mocked:
            tts.synthesize_for_track("xin chào", "ai", "out.mp3")
        mocked.assert_called_once_with("xin chào", "out.mp3",
                                       voice_id="voice1", speed=1.5)

    def test_drama_track_uses_drama_voice_and_speed(self):
        with patch.multiple(tts.config, TTS_VOICE_ID_AI="voice1",
                            TTS_VOICE_SPEED_AI=1.5, TTS_VOICE_ID_DRAMA="preset_my_duyen",
                            TTS_VOICE_SPEED_DRAMA=1.0), \
             patch.object(tts, "text_to_speech", return_value="out.mp3") as mocked:
            tts.synthesize_for_track("kịch bản", "drama", "out.mp3")
        mocked.assert_called_once_with("kịch bản", "out.mp3",
                                       voice_id="preset_my_duyen", speed=1.0)

    def test_empty_voice_falls_back_to_none_keeps_speed(self):
        # Empty string voice → None (không gửi "" cho provider), speed vẫn giữ.
        with patch.multiple(tts.config, TTS_VOICE_ID_AI="", TTS_VOICE_SPEED_AI=2.0), \
             patch.object(tts, "text_to_speech", return_value="out.mp3") as mocked:
            tts.synthesize_for_track("xin chào", "ai", "out.mp3")
        mocked.assert_called_once_with("xin chào", "out.mp3", voice_id=None, speed=2.0)

    def test_unknown_track_uses_global_defaults(self):
        with patch.multiple(tts.config, TTS_VOICE_ID="preset_my_duyen", TTS_VOICE_SPEED=1.0), \
             patch.object(tts, "text_to_speech", return_value="out.mp3") as mocked:
            tts.synthesize_for_track("xin chào", "unknown_track", "out.mp3")
        mocked.assert_called_once_with("xin chào", "out.mp3",
                                       voice_id="preset_my_duyen", speed=1.0)


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


class TestApiVersionDispatch(unittest.TestCase):
    """_use_v2(): chỉ "v2" rõ ràng mới bật endpoint đồng bộ; còn lại là job API v1."""

    def test_v1_selects_job_api(self):
        with patch.object(tts.config, "TTS_API_VERSION", "v1"):
            self.assertFalse(tts._use_v2())

    def test_v2_selects_sync_endpoint(self):
        with patch.object(tts.config, "TTS_API_VERSION", "v2"):
            self.assertTrue(tts._use_v2())

    def test_value_is_normalised(self):
        for value in ("V2", " v2 ", "V2\n"):
            with patch.object(tts.config, "TTS_API_VERSION", value):
                self.assertTrue(tts._use_v2(), value)

    def test_unknown_value_falls_back_to_v1(self):
        # Gõ sai không được đẩy pipeline sang v2 (bắt buộc bearer token).
        for value in ("v3", "", None):
            with patch.object(tts.config, "TTS_API_VERSION", value):
                self.assertFalse(tts._use_v2(), value)

    def test_tts_single_routes_to_job_api_by_default(self):
        with patch.object(tts.config, "TTS_API_VERSION", "v1"), \
             patch.object(tts, "_tts_v2_single") as v2, \
             patch.object(tts, "_submit_job", return_value=None) as submit:
            result = tts._tts_single("xin chào", "out.mp3",
                                     voice_id="voice1", speed=1.5)
        self.assertIsNone(result)           # submit trả None → fail fast
        v2.assert_not_called()              # không đụng endpoint v2
        submit.assert_called_once()

    def test_tts_single_routes_to_v2_when_selected(self):
        with patch.object(tts.config, "TTS_API_VERSION", "v2"), \
             patch.object(tts, "_tts_v2_single", return_value="out.mp3") as v2, \
             patch.object(tts, "_submit_job") as submit:
            result = tts._tts_single("xin chào", "out.mp3",
                                     voice_id="voice1", speed=0.8)
        self.assertEqual(result, "out.mp3")
        submit.assert_not_called()          # không đụng flow job
        v2.assert_called_once_with("xin chào", "out.mp3",
                                   voice_id="voice1", speed=0.8)


class _V2Base(unittest.TestCase):
    """Config chung cho các test flow v2 (endpoint đồng bộ)."""

    def setUp(self):
        self.cfg = patch.multiple(
            tts.config,
            TTS_API_VERSION="v2",
            TTS_V2_API_URL="https://tts2.nuitruc.ai/v1/audio/speech",
            TTS_API_KEY="nt_sec_testtoken",
            TTS_V2_MODEL="nuitruc-tts-v2",
            TTS_V2_CFG_VALUE=2.0,
            TTS_V2_INFERENCE_TIMESTEPS=10,
            TTS_VOICE_ID="preset_my_duyen",
            TTS_VOICE_SPEED=1.0,
            TTS_ALLOW_INSECURE_SSL=False,
        )
        self.cfg.start()
        self.addCleanup(self.cfg.stop)
        patch.object(tts.time, "sleep").start()
        self.addCleanup(patch.stopall)
        self.out = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False).name
        self.addCleanup(lambda: os.path.exists(self.out) and os.remove(self.out))

    def _run(self, opener, **kwargs):
        with patch.object(tts, "_build_opener", return_value=opener):
            return tts._tts_single("xin chào thế giới", self.out, **kwargs)


class TestV2HappyPath(_V2Base):
    def test_single_request_writes_audio(self):
        opener = _FakeOpener({"/v1/audio/speech":
                              [_FakeResp(b"ID3AUDIO",
                                         {"Content-Type": "audio/mpeg"})._body]})
        result = self._run(opener)
        self.assertEqual(result, self.out)
        with open(self.out, "rb") as f:
            self.assertEqual(f.read(), b"ID3AUDIO")
        # ĐÚNG MỘT request: không submit/status/result nữa.
        self.assertEqual(len(opener.calls), 1)
        self.assertEqual(opener.calls[0],
                         "https://tts2.nuitruc.ai/v1/audio/speech")


class TestV2Payload(_V2Base):
    """Body phải khớp đúng bộ field API v2 nhận (curl mẫu + speed)."""

    def _capture(self, **kwargs):
        captured = {}

        class CapturingOpener:
            def open(self, req, timeout=None):
                captured["body"] = json.loads(req.data)
                captured["headers"] = dict(req.headers)
                captured["timeout"] = timeout
                return _FakeResp(b"AUDIO", {"Content-Type": "audio/mpeg"})

        self._run(CapturingOpener(), **kwargs)
        return captured

    def test_payload_fields_match_v2_contract(self):
        body = self._capture(voice_id="voice1", speed=0.8)["body"]
        self.assertEqual(body, {
            "model": "nuitruc-tts-v2",
            "input": "xin chào thế giới",
            "voice": "voice1",
            "cfg_value": 2.0,
            "inference_timesteps": 10,
            "speed": 0.8,
        })
        # Tên field v1 KHÔNG được sót lại.
        self.assertNotIn("text", body)
        self.assertNotIn("voice_id", body)

    def test_speed_none_falls_back_to_config(self):
        body = self._capture(voice_id="voice1", speed=None)["body"]
        self.assertEqual(body["speed"], 1.0)

    def test_voice_none_falls_back_to_config(self):
        body = self._capture(voice_id=None)["body"]
        self.assertEqual(body["voice"], "preset_my_duyen")

    def test_bearer_token_sent(self):
        headers = self._capture()["headers"]
        # urllib viết hoa chữ cái đầu tên header.
        self.assertEqual(headers.get("Authorization"), "Bearer nt_sec_testtoken")

    def test_uses_v2_timeout(self):
        with patch.object(tts, "TTS_V2_TIMEOUT", 300):
            self.assertEqual(self._capture()["timeout"], 300)


class TestV2FailureModes(_V2Base):
    def test_missing_api_key_fails_before_any_request(self):
        opener = _FakeOpener({"/speech": [b"AUDIO"]})
        with patch.object(tts.config, "TTS_API_KEY", ""):
            with self.assertLogs(tts.logger, level="ERROR") as cm:
                result = self._run(opener)
        self.assertIsNone(result)
        self.assertEqual(opener.calls, [])   # không gửi request để ăn 401
        self.assertTrue(any("TTS_API_KEY" in m for m in cm.output))

    def test_missing_endpoint_fails(self):
        opener = _FakeOpener({"/speech": [b"AUDIO"]})
        with patch.object(tts.config, "TTS_V2_API_URL", ""):
            result = self._run(opener)
        self.assertIsNone(result)
        self.assertEqual(opener.calls, [])

    def test_json_error_with_http_200_is_not_written_as_audio(self):
        opener = _FakeOpener({"/v1/audio/speech":
                              [_json({"error": "voice not found"})]})
        with self.assertLogs(tts.logger, level="ERROR"):
            result = self._run(opener)
        self.assertIsNone(result)
        self.assertEqual(os.path.getsize(self.out), 0)   # file không bị ghi rác

    def test_json_error_detected_with_lowercase_header(self):
        # Tên header không phân biệt hoa/thường; đừng phụ thuộc "Content-Type".
        class LowerHeaderOpener:
            def open(self, req, timeout=None):
                return _FakeResp(b"not audio at all",
                                 {"content-type": "application/json"})

        with self.assertLogs(tts.logger, level="ERROR"):
            result = self._run(LowerHeaderOpener())
        self.assertIsNone(result)

    def test_json_error_with_leading_whitespace_or_bom_is_detected(self):
        """Body JSON mở đầu bằng BOM/xuống dòng vẫn phải bị bắt, không ghi ra .mp3."""
        for raw in (b"\n  " + _json({"error": "nope"}),
                    b"\xef\xbb\xbf" + _json({"error": "nope"}),
                    b"\r\n[]"):
            with self.subTest(raw=raw[:6]):
                opener = _FakeOpener({"/v1/audio/speech": [raw]})
                with self.assertLogs(tts.logger, level="ERROR"):
                    result = self._run(opener)
                self.assertIsNone(result)
                self.assertEqual(os.path.getsize(self.out), 0)

    def test_non_audio_log_hides_token_echoed_by_gateway(self):
        # Gateway dội lại request headers trong body: token không được vào log.
        echoed = _json({"error": "bad request",
                        "headers": {"authorization": "Bearer nt_sec_testtoken"}})
        opener = _FakeOpener({"/v1/audio/speech": [echoed]})
        with self.assertLogs(tts.logger, level="ERROR") as cm:
            result = self._run(opener)
        self.assertIsNone(result)
        self.assertNotIn("nt_sec_testtoken", "\n".join(cm.output))

    def test_empty_body_fails_over(self):
        opener = _FakeOpener({"/v1/audio/speech": [b""]})
        with self.assertLogs(tts.logger, level="ERROR"):
            result = self._run(opener)
        self.assertIsNone(result)

    def test_http_400_logs_server_reason_without_token(self):
        """400 (vd voice id sai) phải hiện lý do THẬT từ body, không lộ token."""
        import io
        err = HTTPError("https://tts2.nuitruc.ai/v1/audio/speech", 400, "Bad Request",
                        {}, io.BytesIO(b'{"detail":"unknown voice: preset_my_duyen"}'))
        opener = _FakeOpener({"/v1/audio/speech": [err]})
        with patch.object(tts, "TTS_MAX_RETRIES", 1):
            with self.assertLogs(tts.logger, level="ERROR") as cm:
                result = self._run(opener)
        self.assertIsNone(result)
        joined = "\n".join(cm.output)
        self.assertIn("unknown voice", joined)
        self.assertNotIn("nt_sec_testtoken", joined)

    def test_timeout_is_not_retried(self):
        opener = _FakeOpener({"/v1/audio/speech": [TimeoutError("timed out")]})
        with patch.object(tts, "TTS_MAX_RETRIES", 3):
            with self.assertLogs(tts.logger, level="ERROR"):
                result = self._run(opener)
        self.assertIsNone(result)
        self.assertEqual(len(opener.calls), 1)


class TestRedact(unittest.TestCase):
    """Token không bao giờ được ghi vào log, kể cả khi server dội nó lại."""

    def test_configured_token_is_masked(self):
        with patch.object(tts.config, "TTS_API_KEY", "nt_sec_secret"):
            self.assertNotIn("nt_sec_secret", tts._redact("key=nt_sec_secret"))

    def test_bearer_shaped_value_is_masked_even_if_unknown(self):
        with patch.object(tts.config, "TTS_API_KEY", ""):
            out = tts._redact('{"authorization": "Bearer nt_sec_other"}')
        self.assertNotIn("nt_sec_other", out)
        self.assertIn("Bearer ***", out)

    def test_error_detail_masks_body(self):
        import io
        err = HTTPError("https://x", 400, "Bad Request", {},
                        io.BytesIO(b'{"sent":"Bearer nt_sec_leak"}'))
        with patch.object(tts.config, "TTS_API_KEY", "nt_sec_leak"):
            detail = tts._error_detail(err)
        self.assertNotIn("nt_sec_leak", detail)


class TestNuiTrucProviderVersionGuard(unittest.TestCase):
    """Provider guard phải kiểm tra URL của ĐÚNG version đang dùng."""

    def test_v2_runs_even_with_empty_v1_url(self):
        from video.tts.nuitruc import NuiTrucProvider
        with patch.multiple(tts.config, TTS_API_VERSION="v2", TTS_API_URL="",
                            TTS_V2_API_URL="https://tts2.nuitruc.ai/v1/audio/speech"), \
             patch.object(tts, "_tts_v2_single", return_value="out.mp3") as v2:
            result = NuiTrucProvider().synthesize("xin chào", "out.mp3")
        self.assertEqual(result, "out.mp3")
        v2.assert_called_once()

    def test_v2_without_endpoint_returns_none(self):
        from video.tts.nuitruc import NuiTrucProvider
        with patch.multiple(tts.config, TTS_API_VERSION="v2", TTS_V2_API_URL=""), \
             patch.object(tts, "_tts_v2_single") as v2:
            result = NuiTrucProvider().synthesize("xin chào", "out.mp3")
        self.assertIsNone(result)
        v2.assert_not_called()


if __name__ == "__main__":
    unittest.main()
