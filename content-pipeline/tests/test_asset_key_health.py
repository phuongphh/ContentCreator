"""Tests for video/asset_key_health.py (follow-up #94 — media API key monitoring)."""
from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import storage.database as db
import storage.migrate as migrate
import video.asset_key_health as ah


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://api.example.com/x", code, "err", {},
                                  io.BytesIO(b""))


def _ok_urlopen():
    cm = MagicMock()
    cm.__enter__.return_value.read.return_value = b"{"
    return MagicMock(return_value=cm)


class TestProbe(unittest.TestCase):
    def test_200_is_ok(self):
        with patch.object(ah.urllib.request, "urlopen", _ok_urlopen()):
            code, _ = ah._probe("https://x", {"Authorization": "k"}, 5)
        self.assertEqual(code, ah.OK)

    def test_401_is_invalid(self):
        with patch.object(ah.urllib.request, "urlopen",
                          MagicMock(side_effect=_http_error(401))):
            code, _ = ah._probe("https://x", {}, 5)
        self.assertEqual(code, ah.INVALID)

    def test_403_is_invalid(self):
        with patch.object(ah.urllib.request, "urlopen",
                          MagicMock(side_effect=_http_error(403))):
            code, _ = ah._probe("https://x", {}, 5)
        self.assertEqual(code, ah.INVALID)

    def test_500_is_transient(self):
        with patch.object(ah.urllib.request, "urlopen",
                          MagicMock(side_effect=_http_error(503))):
            code, _ = ah._probe("https://x", {}, 5)
        self.assertEqual(code, ah.TRANSIENT)

    def test_429_is_transient(self):
        with patch.object(ah.urllib.request, "urlopen",
                          MagicMock(side_effect=_http_error(429))):
            code, _ = ah._probe("https://x", {}, 5)
        self.assertEqual(code, ah.TRANSIENT)

    def test_timeout_is_transient(self):
        with patch.object(ah.urllib.request, "urlopen",
                          MagicMock(side_effect=TimeoutError("t"))):
            code, _ = ah._probe("https://x", {}, 5)
        self.assertEqual(code, ah.TRANSIENT)

    def test_urlerror_is_transient(self):
        with patch.object(ah.urllib.request, "urlopen",
                          MagicMock(side_effect=urllib.error.URLError("dns"))):
            code, _ = ah._probe("https://x", {}, 5)
        self.assertEqual(code, ah.TRANSIENT)


class TestCheckPexels(unittest.TestCase):
    def test_missing_key(self):
        with patch.object(ah.config, "PEXELS_API_KEY", ""):
            res = ah.check_pexels()
        self.assertEqual(res.code, ah.MISSING)

    def test_ok_and_uses_raw_auth_header(self):
        opener = _ok_urlopen()
        with patch.object(ah.config, "PEXELS_API_KEY", "pk_live"), \
             patch.object(ah.urllib.request, "urlopen", opener):
            res = ah.check_pexels()
        self.assertEqual(res.code, ah.OK)
        req = opener.call_args[0][0]
        # Pexels uses the raw key (NOT "Bearer ...")
        self.assertEqual(req.headers["Authorization"], "pk_live")

    def test_invalid_key(self):
        with patch.object(ah.config, "PEXELS_API_KEY", "bad"), \
             patch.object(ah.urllib.request, "urlopen",
                          MagicMock(side_effect=_http_error(401))):
            res = ah.check_pexels()
        self.assertEqual(res.code, ah.INVALID)


class TestCheckReplicate(unittest.TestCase):
    def test_empty_token_is_disabled(self):
        with patch.object(ah.config, "REPLICATE_API_TOKEN", ""):
            res = ah.check_replicate()
        self.assertEqual(res.code, ah.DISABLED)
        self.assertTrue(res.healthy)  # optional feature off is not a failure

    def test_ok_and_uses_bearer_header(self):
        opener = _ok_urlopen()
        with patch.object(ah.config, "REPLICATE_API_TOKEN", "r8_tok"), \
             patch.object(ah.urllib.request, "urlopen", opener):
            res = ah.check_replicate()
        self.assertEqual(res.code, ah.OK)
        req = opener.call_args[0][0]
        self.assertEqual(req.headers["Authorization"], "Bearer r8_tok")

    def test_invalid_token(self):
        with patch.object(ah.config, "REPLICATE_API_TOKEN", "r8_bad"), \
             patch.object(ah.urllib.request, "urlopen",
                          MagicMock(side_effect=_http_error(401))):
            res = ah.check_replicate()
        self.assertEqual(res.code, ah.INVALID)


class TestCheckAll(unittest.TestCase):
    def test_default_checks_both_providers(self):
        with patch.object(ah, "check_pexels",
                          return_value=ah.KeyCheckResult("pexels", ah.OK)), \
             patch.object(ah, "check_replicate",
                          return_value=ah.KeyCheckResult("replicate", ah.DISABLED)):
            results = ah.check_all()
        self.assertEqual({r.provider for r in results}, {"pexels", "replicate"})


class _AlertBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dbpath = os.path.join(self.tmp, "test.db")
        self._patch = patch.object(db.config, "DB_PATH", self.dbpath)
        self._patch.start()
        db.init_db()
        migrate.migrate_up()

    def tearDown(self):
        self._patch.stop()

    def _res(self, provider, code):
        return ah.KeyCheckResult(provider, code, "detail")


class TestCheckAndAlert(_AlertBase):
    def test_pexels_invalid_alerts_with_link(self):
        with patch.object(ah, "check_all", return_value=[self._res("pexels", ah.INVALID)]), \
             patch("notifier.telegram_bot.send_alert") as alert:
            ah.check_and_alert()
        alert.assert_called_once()
        self.assertIn("pexels.com/api", alert.call_args[0][0])

    def test_pexels_missing_alerts(self):
        with patch.object(ah, "check_all", return_value=[self._res("pexels", ah.MISSING)]), \
             patch("notifier.telegram_bot.send_alert") as alert:
            ah.check_and_alert()
        alert.assert_called_once()

    def test_replicate_disabled_no_alert(self):
        with patch.object(ah, "check_all", return_value=[self._res("replicate", ah.DISABLED)]), \
             patch("notifier.telegram_bot.send_alert") as alert:
            ah.check_and_alert()
        alert.assert_not_called()

    def test_replicate_invalid_alerts_with_link(self):
        with patch.object(ah, "check_all", return_value=[self._res("replicate", ah.INVALID)]), \
             patch("notifier.telegram_bot.send_alert") as alert:
            ah.check_and_alert()
        alert.assert_called_once()
        self.assertIn("replicate.com/account", alert.call_args[0][0])

    def test_ok_resets_transient_counter(self):
        ah._set_transient_count("pexels", 2)
        with patch.object(ah, "check_all", return_value=[self._res("pexels", ah.OK)]), \
             patch("notifier.telegram_bot.send_alert") as alert:
            ah.check_and_alert()
        alert.assert_not_called()
        self.assertEqual(ah._get_transient_count("pexels"), 0)

    def test_transient_alerts_once_at_threshold(self):
        with patch.object(ah.config, "ASSET_KEY_HEALTH_TRANSIENT_ALERT_AFTER", 2), \
             patch.object(ah, "check_all", return_value=[self._res("pexels", ah.TRANSIENT)]), \
             patch("notifier.telegram_bot.send_alert") as alert:
            ah.check_and_alert()  # 1
            ah.check_and_alert()  # 2 == threshold → alert
            ah.check_and_alert()  # 3 no repeat
        alert.assert_called_once()

    def test_alert_send_failure_swallowed(self):
        with patch.object(ah, "check_all", return_value=[self._res("pexels", ah.INVALID)]), \
             patch("notifier.telegram_bot.send_alert", side_effect=RuntimeError("net")):
            results = ah.check_and_alert()
        self.assertEqual(results[0].code, ah.INVALID)


if __name__ == "__main__":
    unittest.main()
