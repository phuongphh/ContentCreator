"""Tests cho xử lý credential bị từ chối ở tầng collector (issue #117).

Log 29/08 có "Twitter API error: HTTP Error 401" và "Product Hunt API error:
HTTP Error 401" mỗi sáng: nguồn chết hẳn nhưng chỉ nằm trong file log, collector
trả 0 y như một ngày không có tin. Nay 401/403 → `CollectorAuthError` nổi lên
`main.run_pipeline` để vào pipeline summary Telegram; lỗi TẠM THỜI (5xx/mạng)
vẫn im lặng trả 0 như cũ.
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import collectors.producthunt_collector as ph
import collectors.twitter_collector as twitter
from collectors.errors import CollectorAuthError


def _http_error(code: int) -> HTTPError:
    return HTTPError("https://api.example.com", code, "Unauthorized", {}, None)


def _ok_response(payload: dict):
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode()
    resp.__enter__ = lambda self: self
    resp.__exit__ = lambda self, *a: False
    return resp


class TestCollectorAuthError(unittest.TestCase):
    def test_message_is_actionable_and_has_no_secret(self):
        err = CollectorAuthError("Twitter API", 401, "TWITTER_BEARER_TOKEN hết hạn")
        self.assertIn("401", str(err))
        self.assertIn("TWITTER_BEARER_TOKEN", str(err))
        self.assertEqual(err.status, 401)
        self.assertEqual(err.source, "Twitter API")


class TestTwitterAuth(unittest.TestCase):
    def setUp(self):
        token = patch.object(twitter.config, "TWITTER_BEARER_TOKEN", "fake-token")
        token.start()
        self.addCleanup(token.stop)

    def test_401_raises_collector_auth_error(self):
        with patch.object(twitter, "urlopen", side_effect=_http_error(401)):
            with self.assertRaises(CollectorAuthError) as ctx:
                twitter._api_request("users/by/username/OpenAI", {})
        self.assertIn("TWITTER_BEARER_TOKEN", str(ctx.exception))

    def test_403_raises_collector_auth_error(self):
        with patch.object(twitter, "urlopen", side_effect=_http_error(403)):
            with self.assertRaises(CollectorAuthError):
                twitter._api_request("users/by/username/OpenAI", {})

    def test_transient_http_error_returns_none(self):
        for code in (429, 500, 503):
            with self.subTest(code=code):
                with patch.object(twitter, "urlopen", side_effect=_http_error(code)):
                    self.assertIsNone(twitter._api_request("x", {}))

    def test_network_error_returns_none(self):
        with patch.object(twitter, "urlopen", side_effect=URLError("timeout")):
            self.assertIsNone(twitter._api_request("x", {}))

    def test_collect_all_propagates_auth_error_and_fails_fast(self):
        """Token chết: 1 request duy nhất, không nã 2 request × 5 account."""
        with patch.object(twitter, "urlopen", side_effect=_http_error(401)) as opener:
            with self.assertRaises(CollectorAuthError):
                twitter.collect_all_twitter()
        self.assertEqual(opener.call_count, 1)

    def test_collect_all_returns_zero_on_transient_error(self):
        with patch.object(twitter, "urlopen", side_effect=_http_error(503)):
            self.assertEqual(twitter.collect_all_twitter(), 0)

    def test_missing_token_is_not_an_error(self):
        """Chưa cấu hình = nguồn tắt có chủ đích — không làm phiền chủ kênh."""
        with patch.object(twitter.config, "TWITTER_BEARER_TOKEN", ""):
            with patch.object(twitter, "urlopen") as opener:
                self.assertEqual(twitter.collect_all_twitter(), 0)
            opener.assert_not_called()

    def test_auth_error_mid_run_stops_remaining_accounts(self):
        calls = {"n": 0}

        def _fake_request(endpoint, params):
            calls["n"] += 1
            if calls["n"] == 1:      # _validate_token
                return {"data": {"id": "1"}}
            raise CollectorAuthError("Twitter API", 401, "token bị thu hồi")

        with patch.object(twitter, "_api_request", side_effect=_fake_request):
            with self.assertRaises(CollectorAuthError):
                twitter.collect_all_twitter()
        self.assertEqual(calls["n"], 2, "dừng ngay ở account đầu tiên")


class TestProductHuntAuth(unittest.TestCase):
    def setUp(self):
        token = patch.object(ph.config, "PRODUCTHUNT_API_TOKEN", "fake-token")
        token.start()
        self.addCleanup(token.stop)

    def test_401_raises_collector_auth_error(self):
        with patch.object(ph, "urlopen", side_effect=_http_error(401)):
            with self.assertRaises(CollectorAuthError) as ctx:
                ph.collect_producthunt()
        self.assertIn("PRODUCTHUNT_API_TOKEN", str(ctx.exception))

    def test_403_raises_collector_auth_error(self):
        with patch.object(ph, "urlopen", side_effect=_http_error(403)):
            with self.assertRaises(CollectorAuthError):
                ph.collect_producthunt()

    def test_transient_error_returns_zero(self):
        with patch.object(ph, "urlopen", side_effect=_http_error(500)):
            self.assertEqual(ph.collect_producthunt(), 0)
        with patch.object(ph, "urlopen", side_effect=URLError("timeout")):
            self.assertEqual(ph.collect_producthunt(), 0)

    def test_missing_token_is_not_an_error(self):
        with patch.object(ph.config, "PRODUCTHUNT_API_TOKEN", ""):
            with patch.object(ph, "urlopen") as opener:
                self.assertEqual(ph.collect_producthunt(), 0)
            opener.assert_not_called()


if __name__ == "__main__":
    unittest.main()
