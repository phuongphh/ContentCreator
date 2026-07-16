"""Tests for publisher/token_health.py (issue #94 — OAuth token monitoring)."""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import storage.database as db
import storage.migrate as migrate
import publisher.token_health as th


def _http_error(code: int, body: bytes) -> urllib.error.HTTPError:
    """Build a urllib HTTPError whose .read() yields `body`."""
    return urllib.error.HTTPError("https://oauth2.googleapis.com/token", code,
                                  "err", {}, io.BytesIO(body))


def _ok_urlopen():
    """A urlopen() replacement returning a 200 context manager."""
    cm = MagicMock()
    cm.__enter__.return_value.read.return_value = b"{"
    return MagicMock(return_value=cm)


_VALID_TOKEN = {
    "refresh_token": "1//rt",
    "client_id": "cid.apps.googleusercontent.com",
    "client_secret": "secret",
    "token_uri": "https://oauth2.googleapis.com/token",
}


class TestReadTokenFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_missing_file(self):
        data, code = th._read_token_file(os.path.join(self.tmp, "nope.json"))
        self.assertIsNone(data)
        self.assertEqual(code, th.MISSING)

    def test_empty_path(self):
        data, code = th._read_token_file("")
        self.assertEqual(code, th.MISSING)

    def test_bad_json(self):
        p = os.path.join(self.tmp, "bad.json")
        with open(p, "w") as f:
            f.write("{not json")
        data, code = th._read_token_file(p)
        self.assertIsNone(data)
        self.assertEqual(code, th.UNREADABLE)

    def test_non_object_json(self):
        p = os.path.join(self.tmp, "list.json")
        with open(p, "w") as f:
            f.write("[1, 2]")
        _, code = th._read_token_file(p)
        self.assertEqual(code, th.UNREADABLE)

    def test_valid(self):
        p = os.path.join(self.tmp, "ok.json")
        with open(p, "w") as f:
            json.dump(_VALID_TOKEN, f)
        data, code = th._read_token_file(p)
        self.assertIsNone(code)
        self.assertEqual(data["client_id"], _VALID_TOKEN["client_id"])


class TestClassifyHttpError(unittest.TestCase):
    def test_invalid_grant_is_revoked(self):
        code, detail = th._classify_http_error(
            _http_error(400, b'{"error":"invalid_grant","error_description":"expired"}'))
        self.assertEqual(code, th.REVOKED)
        self.assertIn("expired", detail)

    def test_invalid_client_is_misconfig(self):
        code, _ = th._classify_http_error(
            _http_error(401, b'{"error":"invalid_client"}'))
        self.assertEqual(code, th.MISCONFIG)

    def test_5xx_is_transient(self):
        code, _ = th._classify_http_error(_http_error(503, b"upstream down"))
        self.assertEqual(code, th.TRANSIENT)

    def test_429_is_transient(self):
        code, _ = th._classify_http_error(_http_error(429, b""))
        self.assertEqual(code, th.TRANSIENT)

    def test_other_400_is_misconfig(self):
        code, _ = th._classify_http_error(
            _http_error(400, b'{"error":"invalid_request"}'))
        self.assertEqual(code, th.MISCONFIG)


class TestProbeRefresh(unittest.TestCase):
    def test_no_refresh_token(self):
        code, _ = th._probe_refresh({"client_id": "x", "client_secret": "y"}, 5)
        self.assertEqual(code, th.NO_REFRESH_TOKEN)

    def test_missing_client_creds(self):
        code, _ = th._probe_refresh({"refresh_token": "rt"}, 5)
        self.assertEqual(code, th.MISCONFIG)

    def test_200_is_ok(self):
        with patch.object(th.urllib.request, "urlopen", _ok_urlopen()):
            code, _ = th._probe_refresh(_VALID_TOKEN, 5)
        self.assertEqual(code, th.OK)

    def test_invalid_grant_surfaces_revoked(self):
        boom = MagicMock(side_effect=_http_error(400, b'{"error":"invalid_grant"}'))
        with patch.object(th.urllib.request, "urlopen", boom):
            code, _ = th._probe_refresh(_VALID_TOKEN, 5)
        self.assertEqual(code, th.REVOKED)

    def test_timeout_is_transient(self):
        boom = MagicMock(side_effect=TimeoutError("timed out"))
        with patch.object(th.urllib.request, "urlopen", boom):
            code, _ = th._probe_refresh(_VALID_TOKEN, 5)
        self.assertEqual(code, th.TRANSIENT)

    def test_urlerror_is_transient(self):
        boom = MagicMock(side_effect=urllib.error.URLError("dns"))
        with patch.object(th.urllib.request, "urlopen", boom):
            code, _ = th._probe_refresh(_VALID_TOKEN, 5)
        self.assertEqual(code, th.TRANSIENT)

    def test_default_token_uri_when_missing(self):
        token = dict(_VALID_TOKEN)
        del token["token_uri"]
        opener = _ok_urlopen()
        with patch.object(th.urllib.request, "urlopen", opener):
            th._probe_refresh(token, 5)
        req = opener.call_args[0][0]
        self.assertEqual(req.full_url, th._DEFAULT_TOKEN_URI)


class TestCheckChannel(unittest.TestCase):
    def test_missing_token_file(self):
        with patch.object(th, "resolve_token_file", return_value="/nope/x.json"):
            res = th.check_channel("drama_youtube")
        self.assertEqual(res.code, th.MISSING)
        self.assertEqual(res.channel_key, "drama_youtube")

    def test_ok_via_probe(self):
        with patch.object(th, "resolve_token_file", return_value="/t.json"), \
             patch.object(th, "_read_token_file", return_value=(_VALID_TOKEN, None)), \
             patch.object(th, "_probe_refresh", return_value=(th.OK, "")):
            res = th.check_channel("ai_youtube")
        self.assertTrue(res.healthy)


class TestCheckAll(unittest.TestCase):
    def test_defaults_to_all_youtube_channels(self):
        with patch.object(th, "resolve_token_file", side_effect=lambda k: f"/{k}.json"), \
             patch.object(th, "_check_token_file", return_value=(th.OK, "")):
            results = th.check_all()
        keys = {r.channel_key for r in results}
        self.assertIn("ai_youtube", keys)
        self.assertIn("drama_youtube", keys)
        self.assertNotIn("tiktok_main", keys)  # not a youtube channel

    def test_dedupes_probe_by_resolved_path(self):
        # Both channels fall back to the same token file → probe only once.
        probe = MagicMock(return_value=(th.OK, ""))
        with patch.object(th, "resolve_token_file", return_value="/shared.json"), \
             patch.object(th, "_check_token_file", probe):
            results = th.check_all(["ai_youtube", "drama_youtube"])
        self.assertEqual(len(results), 2)
        probe.assert_called_once()


class _AlertTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dbpath = os.path.join(self.tmp, "test.db")
        self._patch = patch.object(db.config, "DB_PATH", self.dbpath)
        self._patch.start()
        db.init_db()
        migrate.migrate_up()

    def tearDown(self):
        self._patch.stop()

    def _result(self, code, key="drama_youtube"):
        return th.TokenCheckResult(key, "[2P] Chuyện Đời", "/t.json", code, "detail")


class TestCheckAndAlert(_AlertTestBase):
    def test_revoked_alerts_with_reauth_hint(self):
        with patch.object(th, "check_all", return_value=[self._result(th.REVOKED)]), \
             patch("notifier.telegram_bot.send_alert") as alert:
            th.check_and_alert()
        alert.assert_called_once()
        msg = alert.call_args[0][0]
        self.assertIn("invalid_grant", msg)
        self.assertIn("--token-file", msg)  # actionable re-auth command

    def test_missing_alerts(self):
        with patch.object(th, "check_all", return_value=[self._result(th.MISSING)]), \
             patch("notifier.telegram_bot.send_alert") as alert:
            th.check_and_alert()
        alert.assert_called_once()

    def test_ok_does_not_alert_and_resets_counter(self):
        th._set_transient_count("drama_youtube", 2)
        with patch.object(th, "check_all", return_value=[self._result(th.OK)]), \
             patch("notifier.telegram_bot.send_alert") as alert:
            th.check_and_alert()
        alert.assert_not_called()
        self.assertEqual(th._get_transient_count("drama_youtube"), 0)

    def test_transient_below_threshold_no_alert(self):
        with patch.object(th.config, "TOKEN_HEALTH_TRANSIENT_ALERT_AFTER", 3), \
             patch.object(th, "check_all", return_value=[self._result(th.TRANSIENT)]), \
             patch("notifier.telegram_bot.send_alert") as alert:
            th.check_and_alert()  # count 1
            th.check_and_alert()  # count 2
        alert.assert_not_called()
        self.assertEqual(th._get_transient_count("drama_youtube"), 2)

    def test_transient_alerts_once_at_threshold(self):
        with patch.object(th.config, "TOKEN_HEALTH_TRANSIENT_ALERT_AFTER", 2), \
             patch.object(th, "check_all", return_value=[self._result(th.TRANSIENT)]), \
             patch("notifier.telegram_bot.send_alert") as alert:
            th.check_and_alert()  # count 1, no alert
            th.check_and_alert()  # count 2 == threshold → alert once
            th.check_and_alert()  # count 3, no repeat
        alert.assert_called_once()

    def test_alert_send_failure_is_swallowed(self):
        with patch.object(th, "check_all", return_value=[self._result(th.REVOKED)]), \
             patch("notifier.telegram_bot.send_alert", side_effect=RuntimeError("net")):
            # must not raise
            results = th.check_and_alert()
        self.assertEqual(results[0].code, th.REVOKED)


if __name__ == "__main__":
    unittest.main()
