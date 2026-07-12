"""Tests for collectors/reddit_client.py (issue #78 — Reddit OAuth/HTTP client)."""
from __future__ import annotations

import io
import json
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import collectors.reddit_client as rc


def _http_error(code, headers=None):
    from urllib.error import HTTPError
    return HTTPError(
        url="https://oauth.reddit.com/x", code=code, msg="err",
        hdrs=headers or {}, fp=io.BytesIO(b""),
    )


def _ok_response(payload):
    """A context-manager stand-in for urlopen() returning JSON bytes."""
    cm = MagicMock()
    cm.__enter__.return_value.read.return_value = json.dumps(payload).encode("utf-8")
    cm.__exit__.return_value = False
    return cm


class RedditClientBase(unittest.TestCase):
    def setUp(self):
        rc.reset_state()
        # No real sleeping in tests.
        self._sleep = patch.object(rc.time, "sleep", return_value=None)
        self._sleep.start()

    def tearDown(self):
        self._sleep.stop()
        rc.reset_state()


class TestHasCredentials(RedditClientBase):
    def test_true_when_both_set(self):
        with patch.object(rc.config, "REDDIT_CLIENT_ID", "id"), \
             patch.object(rc.config, "REDDIT_CLIENT_SECRET", "secret"):
            self.assertTrue(rc.has_oauth_credentials())

    def test_false_when_missing(self):
        with patch.object(rc.config, "REDDIT_CLIENT_ID", ""), \
             patch.object(rc.config, "REDDIT_CLIENT_SECRET", "secret"):
            self.assertFalse(rc.has_oauth_credentials())


class TestCollectionEnabled(RedditClientBase):
    def test_disabled_when_flag_off(self):
        with patch.object(rc.config, "REDDIT_ENABLED", False), \
             patch.object(rc.config, "REDDIT_CLIENT_ID", "id"), \
             patch.object(rc.config, "REDDIT_CLIENT_SECRET", "secret"):
            self.assertFalse(rc.collection_enabled())

    def test_disabled_when_enabled_but_no_creds(self):
        # Enabling without credentials must NOT fall through to unauthenticated
        # calls (they re-flag the IP) — collection stays off.
        with patch.object(rc.config, "REDDIT_ENABLED", True), \
             patch.object(rc.config, "REDDIT_CLIENT_ID", ""), \
             patch.object(rc.config, "REDDIT_CLIENT_SECRET", ""):
            self.assertFalse(rc.collection_enabled())

    def test_enabled_when_flag_on_and_creds_present(self):
        with patch.object(rc.config, "REDDIT_ENABLED", True), \
             patch.object(rc.config, "REDDIT_CLIENT_ID", "id"), \
             patch.object(rc.config, "REDDIT_CLIENT_SECRET", "secret"):
            self.assertTrue(rc.collection_enabled())


class TestUnauthenticatedFallback(RedditClientBase):
    def test_hits_public_json_url_without_credentials(self):
        with patch.object(rc.config, "REDDIT_CLIENT_ID", ""), \
             patch.object(rc.config, "REDDIT_CLIENT_SECRET", ""), \
             patch.object(rc, "urlopen", return_value=_ok_response({"ok": 1})) as mock_open:
            result = rc.get_json("/r/AskReddit/top", {"t": "day"})
        self.assertEqual(result, {"ok": 1})
        req = mock_open.call_args[0][0]
        self.assertTrue(req.full_url.startswith("https://www.reddit.com/r/AskReddit/top.json"))
        self.assertIn("t=day", req.full_url)
        # Compliant, non-placeholder User-Agent is always sent.
        self.assertEqual(req.get_header("User-agent"), rc.config.REDDIT_USER_AGENT)
        self.assertIsNone(req.get_header("Authorization"))


class TestOAuthFlow(RedditClientBase):
    def test_uses_bearer_token_and_oauth_host(self):
        token_resp = _ok_response({"access_token": "TOK", "expires_in": 3600})
        data_resp = _ok_response({"data": {"children": []}})
        with patch.object(rc.config, "REDDIT_CLIENT_ID", "id"), \
             patch.object(rc.config, "REDDIT_CLIENT_SECRET", "secret"), \
             patch.object(rc, "urlopen", side_effect=[token_resp, data_resp]) as mock_open:
            result = rc.get_json("/r/ChatGPT/hot", {"limit": 5})
        self.assertEqual(result, {"data": {"children": []}})
        # Second call is the data request against oauth.reddit.com with a bearer.
        data_req = mock_open.call_args_list[1][0][0]
        self.assertTrue(data_req.full_url.startswith("https://oauth.reddit.com/r/ChatGPT/hot"))
        self.assertEqual(data_req.get_header("Authorization"), "Bearer TOK")
        # raw_json=1 auto-added on the OAuth path.
        self.assertIn("raw_json=1", data_req.full_url)

    def test_token_is_cached_across_calls(self):
        token_resp = _ok_response({"access_token": "TOK", "expires_in": 3600})
        d1 = _ok_response({"n": 1})
        d2 = _ok_response({"n": 2})
        with patch.object(rc.config, "REDDIT_CLIENT_ID", "id"), \
             patch.object(rc.config, "REDDIT_CLIENT_SECRET", "secret"), \
             patch.object(rc, "urlopen", side_effect=[token_resp, d1, d2]) as mock_open:
            rc.get_json("/r/a/hot")
            rc.get_json("/r/b/hot")
        # 3 calls total: 1 token + 2 data (token fetched once, then cached).
        self.assertEqual(mock_open.call_count, 3)

    def test_fails_closed_when_token_fetch_fails(self):
        # Creds ARE configured but the token endpoint keeps failing. get_json
        # must NOT fall back to unauthenticated www.reddit.com (that re-flags the
        # IP — Codex review on PR #80) — it fails closed, returning None, and
        # never issues a request to the public endpoint.
        with patch.object(rc.config, "REDDIT_CLIENT_ID", "id"), \
             patch.object(rc.config, "REDDIT_CLIENT_SECRET", "secret"), \
             patch.object(rc.config, "REDDIT_MAX_RETRIES", 2), \
             patch.object(rc, "urlopen", side_effect=_http_error(500)) as mock_open:
            result = rc.get_json("/r/a/top")
        self.assertIsNone(result)
        # Every call was to the OAuth token endpoint; none to www.reddit.com/*.json.
        for call in mock_open.call_args_list:
            req = call[0][0]
            self.assertNotIn("/r/a/top.json", req.full_url)


class TestErrorHandling(RedditClientBase):
    def test_403_is_not_retried(self):
        with patch.object(rc.config, "REDDIT_CLIENT_ID", ""), \
             patch.object(rc.config, "REDDIT_CLIENT_SECRET", ""), \
             patch.object(rc, "urlopen", side_effect=_http_error(403)) as mock_open:
            result = rc.get_json("/r/a/top")
        self.assertIsNone(result)
        self.assertEqual(mock_open.call_count, 1)  # no retry on a hard block

    def test_429_retries_then_gives_up(self):
        with patch.object(rc.config, "REDDIT_CLIENT_ID", ""), \
             patch.object(rc.config, "REDDIT_CLIENT_SECRET", ""), \
             patch.object(rc.config, "REDDIT_MAX_RETRIES", 3), \
             patch.object(rc, "urlopen", side_effect=_http_error(429)) as mock_open:
            result = rc.get_json("/r/a/top")
        self.assertIsNone(result)
        self.assertEqual(mock_open.call_count, 3)

    def test_429_honours_retry_after_header(self):
        # _throttle() also sleeps (to space out calls), so collect ALL sleeps
        # and assert the Retry-After value is among them.
        sleeps = []
        resp = _ok_response({"ok": 1})
        with patch.object(rc.config, "REDDIT_CLIENT_ID", ""), \
             patch.object(rc.config, "REDDIT_CLIENT_SECRET", ""), \
             patch.object(rc.config, "REDDIT_RETRY_AFTER_CAP", 60), \
             patch.object(rc.time, "sleep", side_effect=sleeps.append), \
             patch.object(rc, "urlopen", side_effect=[_http_error(429, {"Retry-After": "7"}), resp]):
            result = rc.get_json("/r/a/top")
        self.assertEqual(result, {"ok": 1})
        self.assertIn(7.0, sleeps)

    def test_retry_after_is_capped(self):
        sleeps = []
        resp = _ok_response({"ok": 1})
        with patch.object(rc.config, "REDDIT_CLIENT_ID", ""), \
             patch.object(rc.config, "REDDIT_CLIENT_SECRET", ""), \
             patch.object(rc.config, "REDDIT_RETRY_AFTER_CAP", 30), \
             patch.object(rc.time, "sleep", side_effect=sleeps.append), \
             patch.object(rc, "urlopen", side_effect=[_http_error(429, {"Retry-After": "9999"}), resp]):
            rc.get_json("/r/a/top")
        self.assertIn(30.0, sleeps)  # capped, not 9999

    def test_401_refreshes_token_once(self):
        token1 = _ok_response({"access_token": "T1", "expires_in": 3600})
        token2 = _ok_response({"access_token": "T2", "expires_in": 3600})
        data_resp = _ok_response({"ok": 1})
        with patch.object(rc.config, "REDDIT_CLIENT_ID", "id"), \
             patch.object(rc.config, "REDDIT_CLIENT_SECRET", "secret"), \
             patch.object(rc, "urlopen",
                          side_effect=[token1, _http_error(401), token2, data_resp]) as mock_open:
            result = rc.get_json("/r/a/hot")
        self.assertEqual(result, {"ok": 1})
        # A fresh token was fetched after the 401.
        self.assertEqual(mock_open.call_count, 4)

    def test_network_error_returns_none_after_retries(self):
        from urllib.error import URLError
        with patch.object(rc.config, "REDDIT_CLIENT_ID", ""), \
             patch.object(rc.config, "REDDIT_CLIENT_SECRET", ""), \
             patch.object(rc.config, "REDDIT_MAX_RETRIES", 2), \
             patch.object(rc, "urlopen", side_effect=URLError("boom")) as mock_open:
            result = rc.get_json("/r/a/top")
        self.assertIsNone(result)
        self.assertEqual(mock_open.call_count, 2)


class TestRetryAfterParsing(RedditClientBase):
    def test_non_numeric_retry_after_ignored(self):
        with patch.object(rc.config, "REDDIT_RETRY_AFTER_CAP", 60):
            err = _http_error(429, {"Retry-After": "Wed, 21 Oct 2099 07:28:00 GMT"})
            self.assertIsNone(rc._parse_retry_after(err))

    def test_missing_header_returns_none(self):
        self.assertIsNone(rc._parse_retry_after(_http_error(429, {})))


if __name__ == "__main__":
    unittest.main()
