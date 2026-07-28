"""Tests for publisher/token_health.py (issue #94 — OAuth token monitoring)."""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta
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
    "scopes": list(th.SCOPES),  # full uploader scopes → healthy
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


class TestScopeCheck(unittest.TestCase):
    """_check_token_file flags a refreshable token that lacks uploader scopes."""

    def test_full_scopes_is_ok(self):
        with patch.object(th, "_read_token_file", return_value=(_VALID_TOKEN, None)), \
             patch.object(th, "_probe_refresh", return_value=(th.OK, "")):
            code, _, _ = th._check_token_file("/t.json", 5)
        self.assertEqual(code, th.OK)

    def test_missing_force_ssl_scope_flagged(self):
        token = dict(_VALID_TOKEN)
        token["scopes"] = ["https://www.googleapis.com/auth/youtube.upload"]  # no force-ssl
        with patch.object(th, "_read_token_file", return_value=(token, None)), \
             patch.object(th, "_probe_refresh", return_value=(th.OK, "")):
            code, detail, _ = th._check_token_file("/t.json", 5)
        self.assertEqual(code, th.MISSING_SCOPES)
        self.assertIn("force-ssl", detail)

    def test_revoked_takes_priority_over_scopes(self):
        token = {"refresh_token": "rt", "client_id": "c", "client_secret": "s"}
        with patch.object(th, "_read_token_file", return_value=(token, None)), \
             patch.object(th, "_probe_refresh", return_value=(th.REVOKED, "invalid_grant")):
            code, _, _ = th._check_token_file("/t.json", 5)
        self.assertEqual(code, th.REVOKED)


class TestCheckAll(unittest.TestCase):
    def test_defaults_to_all_youtube_channels(self):
        with patch.object(th, "resolve_token_file", side_effect=lambda k: f"/{k}.json"), \
             patch.object(th, "_check_token_file", return_value=(th.OK, "", None)):
            results = th.check_all()
        keys = {r.channel_key for r in results}
        self.assertIn("ai_youtube", keys)
        self.assertIn("drama_youtube", keys)
        self.assertNotIn("tiktok_main", keys)  # not a youtube channel

    def test_shared_token_file_is_unconfigured(self):
        # Both channels fall back to the same token file → each is flagged
        # unconfigured (no distinct token), and the shared file is NOT probed
        # (the misconfiguration matters even if that token happens to be valid).
        probe = MagicMock(return_value=(th.OK, "", None))
        with patch.object(th, "resolve_token_file", return_value="/shared.json"), \
             patch.object(th, "_check_token_file", probe):
            results = th.check_all(["ai_youtube", "drama_youtube"])
        self.assertEqual({r.code for r in results}, {th.UNCONFIGURED})
        # each result names the OTHER colliding channel
        by_key = {r.channel_key: r for r in results}
        self.assertIn("drama_youtube", by_key["ai_youtube"].detail)
        self.assertIn("ai_youtube", by_key["drama_youtube"].detail)
        probe.assert_not_called()

    def test_distinct_paths_are_probed(self):
        probe = MagicMock(return_value=(th.OK, "", None))
        with patch.object(th, "resolve_token_file", side_effect=lambda k: f"/{k}.json"), \
             patch.object(th, "_check_token_file", probe):
            results = th.check_all(["ai_youtube", "drama_youtube"])
        self.assertTrue(all(r.healthy for r in results))
        self.assertEqual(probe.call_count, 2)


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

    def _result(self, code, key="drama_youtube", warning=None):
        return th.TokenCheckResult(key, "[2P] Chuyện Đời", "/t.json", code, "detail",
                                   warning)


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

    def test_unconfigured_alerts_with_env_hint(self):
        with patch.object(th, "check_all", return_value=[self._result(th.UNCONFIGURED)]), \
             patch("notifier.telegram_bot.send_alert") as alert:
            th.check_and_alert()
        alert.assert_called_once()
        self.assertIn("YOUTUBE_DRAMA_TOKEN", alert.call_args[0][0])  # env var to set

    def test_missing_scopes_alerts(self):
        with patch.object(th, "check_all", return_value=[self._result(th.MISSING_SCOPES)]), \
             patch("notifier.telegram_bot.send_alert") as alert:
            th.check_and_alert()
        alert.assert_called_once()
        self.assertIn("--force-reauth", alert.call_args[0][0])

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


class TestExpiryWarning(_AlertTestBase):
    """Cảnh báo TRƯỚC khi refresh token hết hạn theo lịch (issue #109).

    Đây là lớp duy nhất đóng được khe mù "probe 08:00 báo OK → upload 12:00
    chết": probe không thể biết trước, chỉ tuổi token mới nói được điều đó.
    """

    def _token_file(self, refresh_token="1//rt", age_days=0.0):
        path = os.path.join(self.tmp, f"tok_{abs(hash(refresh_token))}.json")
        token = dict(_VALID_TOKEN, refresh_token=refresh_token)
        with open(path, "w") as f:
            json.dump(token, f)
        if age_days:
            old = datetime.now() - timedelta(days=age_days)
            os.utime(path, (old.timestamp(), old.timestamp()))
        return path, token

    def test_no_warning_when_token_is_fresh(self):
        path, token = self._token_file()
        self.assertIsNone(th._expiry_warning("ai_youtube", token, path))

    def test_warns_within_window_before_scheduled_expiry(self):
        # Token cấp 6.5 ngày trước, TTL 7 ngày → còn ~12h < ngưỡng 24h.
        path, token = self._token_file(age_days=6.5)
        warning = th._expiry_warning("ai_youtube", token, path)
        self.assertIsNotNone(warning)
        self.assertIn("hết hạn", warning)

    def test_past_ttl_says_can_die_any_moment(self):
        path, token = self._token_file(age_days=8)
        warning = th._expiry_warning("ai_youtube", token, path)
        self.assertIn("quá hạn", warning)

    def test_disabled_when_ttl_zero(self):
        """App đã 'In production' → token không hết hạn theo lịch, tắt cảnh báo."""
        path, token = self._token_file(age_days=30)
        with patch.object(th.config, "YOUTUBE_TOKEN_TTL_DAYS", 0):
            self.assertIsNone(th._expiry_warning("ai_youtube", token, path))

    def test_new_token_resets_the_clock(self):
        """Cấp lại token (refresh_token đổi) → tuổi tính lại từ đầu, hết cảnh báo."""
        old_path, old_token = self._token_file("1//old", age_days=8)
        self.assertIsNotNone(th._expiry_warning("ai_youtube", old_token, old_path))
        new_path, new_token = self._token_file("1//new")
        self.assertIsNone(th._expiry_warning("ai_youtube", new_token, new_path))

    def test_state_never_stores_the_refresh_token(self):
        from storage.pipeline_state import get_state
        path, token = self._token_file("1//supersecret", age_days=6.9)
        th._expiry_warning("ai_youtube", token, path)
        stored = get_state("token_health_minted:ai_youtube") or ""
        self.assertNotIn("supersecret", stored)
        self.assertIn(th._fingerprint("1//supersecret"), stored)

    def test_alert_sent_once_per_day_across_runs(self):
        res = self._result(th.OK, key="ai_youtube", warning="còn ~5 giờ là hết hạn")
        with patch.object(th, "check_all", return_value=[res]), \
             patch("notifier.telegram_bot.send_alert") as alert:
            th.check_and_alert()   # 07:00 ké pipeline
            th.check_and_alert()   # 08:00 cron
            th.check_and_alert()   # 11:30 cron
        alert.assert_called_once()
        msg = alert.call_args[0][0]
        self.assertIn("--force-reauth", msg)
        self.assertIn("YOUTUBE_TOKEN_TTL_DAYS", msg)  # cách tắt khi đã publish app

    def test_failed_send_does_not_burn_the_daily_slot(self):
        """Telegram lỗi lúc 08:00 KHÔNG được nuốt mất cảnh báo 11:30 (Codex #110).

        Cảnh báo này thường chỉ có 1-2 cơ hội trước khi token chết, nên mốc
        dedupe chỉ được ghi sau khi gửi THÀNH CÔNG.
        """
        res = self._result(th.OK, key="ai_youtube", warning="còn ~5 giờ")
        with patch.object(th, "check_all", return_value=[res]), \
             patch("notifier.telegram_bot.send_alert", return_value=False) as alert:
            th.check_and_alert()   # 08:00 — Telegram từ chối
            th.check_and_alert()   # 11:30 — phải thử lại
        self.assertEqual(alert.call_count, 2)

    def test_send_exception_also_leaves_the_slot_open(self):
        res = self._result(th.OK, key="ai_youtube", warning="còn ~5 giờ")
        with patch.object(th, "check_all", return_value=[res]), \
             patch("notifier.telegram_bot.send_alert",
                   side_effect=RuntimeError("net")) as alert:
            th.check_and_alert()
            th.check_and_alert()
        self.assertEqual(alert.call_count, 2)

    def test_stops_repeating_once_delivered(self):
        res = self._result(th.OK, key="ai_youtube", warning="còn ~5 giờ")
        with patch.object(th, "check_all", return_value=[res]), \
             patch("notifier.telegram_bot.send_alert",
                   side_effect=[False, True, True]) as alert:
            th.check_and_alert()   # hỏng → thử lại
            th.check_and_alert()   # tới nơi → ghi mốc
            th.check_and_alert()   # im lặng
        self.assertEqual(alert.call_count, 2)

    def test_warning_does_not_make_channel_unhealthy(self):
        """Token sắp hết hạn vẫn dùng được — không được coi là hỏng."""
        res = self._result(th.OK, key="ai_youtube", warning="còn ~5 giờ")
        self.assertTrue(res.healthy)


class TestIsAuthError(unittest.TestCase):
    """Phân loại lỗi token cho scheduler (issue #109) — một định nghĩa duy nhất."""

    def test_invalid_grant_text_is_auth_error(self):
        exc = RuntimeError("RefreshError: ('invalid_grant: Token has been expired "
                           "or revoked.',)")
        self.assertTrue(th.is_auth_error(exc))

    def test_network_error_is_not_auth_error(self):
        self.assertFalse(th.is_auth_error(TimeoutError("connection timed out")))
        self.assertFalse(th.is_auth_error(OSError("ffmpeg died")))

    def test_reauth_command_targets_the_channel_token_file(self):
        cmd = th.reauth_command("/x/.youtube_token_drama.json")
        self.assertIn("--force-reauth", cmd)
        self.assertIn("/x/.youtube_token_drama.json", cmd)


if __name__ == "__main__":
    unittest.main()
