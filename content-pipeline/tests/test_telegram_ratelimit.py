"""Tests cho tầng rate-limit/retry của Telegram (issue #119).

Root cause #119: 429 bị nuốt như mọi lỗi khác nên tin "video đã đăng" mất
vĩnh viễn dù upload YouTube thành công. Các test dưới khoá lại 3 hành vi:
tôn trọng `retry_after`, giãn nhịp chủ động giữa 2 tin cùng chat, và KHÔNG
thử lại những gì có thể tạo tin/video trùng.
"""
from __future__ import annotations

import io
import os
import sys
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import notifier.telegram_bot as tb


def _http_error(code: int, body: str = "", headers: dict | None = None) -> HTTPError:
    return HTTPError(
        url="https://api.telegram.org/botXXX/sendMessage",
        code=code, msg="err", hdrs=headers or {},
        fp=io.BytesIO(body.encode("utf-8")),
    )


def _rate_limited(retry_after: float | None = 12, header: str | None = None):
    body = '{"ok":false,"error_code":429,"description":"Too Many Requests: retry after 12"'
    if retry_after is not None:
        body += f',"parameters":{{"retry_after":{retry_after}}}'
    body += "}"
    return _http_error(429, body, {"Retry-After": header} if header else None)


def _ok_response(result=None):
    resp = MagicMock()
    resp.read.return_value = ('{"ok":true,"result":%s}' % (result or '{"message_id":7}')).encode()
    resp.__enter__ = lambda s: s
    resp.__exit__ = lambda s, *a: False
    return resp


class _TelegramTest(unittest.TestCase):
    """Config số THẬT + state rate-limit sạch cho mỗi test."""

    def setUp(self):
        self.p_cfg = patch.object(tb, "config")
        self.cfg = self.p_cfg.start()
        self.addCleanup(self.p_cfg.stop)
        self.cfg.TELEGRAM_BOT_TOKEN = "123:SECRET"
        self.cfg.TELEGRAM_CHAT_ID = "chat1"
        self.cfg.TELEGRAM_MIN_SEND_INTERVAL = 1.0
        self.cfg.TELEGRAM_RETRY_AFTER_CAP = 60.0
        self.cfg.TELEGRAM_SEND_RETRIES = 3
        self.cfg.TELEGRAM_RETRY_BUDGET = 90.0
        tb._last_send_at.clear()
        tb._rate_limited_until = 0.0
        tb._conflict_streak = 0
        self.addCleanup(tb._last_send_at.clear)


class TestRetryAfter(_TelegramTest):
    def test_429_waits_then_resends_message_not_lost(self):
        """429 → chờ đúng retry_after → gửi lại → tin KHÔNG mất (root cause #119)."""
        with patch.object(tb, "urlopen", side_effect=[_rate_limited(12), _ok_response()]), \
             patch.object(tb.time, "sleep") as slept:
            ok = tb._send_single_text("video đã đăng")
        self.assertTrue(ok)
        self.assertIn(12.0, [c.args[0] for c in slept.call_args_list])

    def test_retry_after_from_header_when_body_lacks_it(self):
        with patch.object(tb, "urlopen",
                          side_effect=[_rate_limited(None, header="7"), _ok_response()]), \
             patch.object(tb.time, "sleep") as slept:
            self.assertTrue(tb._send_single_text("hi"))
        self.assertIn(7.0, [c.args[0] for c in slept.call_args_list])

    def test_absurd_retry_after_is_capped(self):
        """retry_after điên (1 giờ) không được treo cron — cắt theo cap."""
        with patch.object(tb, "urlopen", side_effect=[_rate_limited(3600), _ok_response()]), \
             patch.object(tb.time, "sleep") as slept:
            self.assertTrue(tb._send_single_text("hi"))
        self.assertEqual(max(c.args[0] for c in slept.call_args_list), 60.0)

    def test_gives_up_within_budget_no_infinite_loop(self):
        """Hết lượt/hết ngân sách thì dừng — không nã Telegram vô hạn."""
        with patch.object(tb, "urlopen", side_effect=_rate_limited(60)), \
             patch.object(tb.time, "sleep") as slept, \
             patch.object(tb.logger, "error") as error:
            self.assertFalse(tb._send_single_text("hi"))
        self.assertLessEqual(sum(c.args[0] for c in slept.call_args_list), 90.0)
        error.assert_called()

    def test_400_is_not_retried(self):
        """Lỗi nghiệp vụ (chat not found) — gửi lại cũng vậy, đừng phí lượt."""
        with patch.object(tb, "urlopen",
                          side_effect=_http_error(400, '{"description":"chat not found"}')) as u, \
             patch.object(tb.logger, "error") as error:
            self.assertFalse(tb._send_single_text("hi"))
        self.assertEqual(u.call_count, 1)
        self.assertIn("chat not found", " ".join(str(a) for a in error.call_args.args))


class TestPacing(_TelegramTest):
    def test_second_message_to_same_chat_is_spaced(self):
        """Cụm tin gửi sát nhau bị giãn ≥ interval — chống tự đâm vào 429."""
        with patch.object(tb, "urlopen", side_effect=[_ok_response(), _ok_response()]), \
             patch.object(tb.time, "sleep") as slept:
            tb._send_single_text("1")
            tb._send_single_text("2")
        waits = [c.args[0] for c in slept.call_args_list]
        self.assertTrue(waits and max(waits) > 0.5, f"không giãn nhịp: {waits}")

    def test_pacing_disabled_by_zero_interval(self):
        self.cfg.TELEGRAM_MIN_SEND_INTERVAL = 0
        with patch.object(tb, "urlopen", side_effect=[_ok_response(), _ok_response()]), \
             patch.object(tb.time, "sleep") as slept:
            tb._send_single_text("1")
            tb._send_single_text("2")
        slept.assert_not_called()

    def test_429_penalty_window_applies_to_next_call(self):
        """Sau 429, call kế tiếp CHỜ hết cửa sổ phạt thay vì nã tiếp
        (nã tiếp chỉ khiến Telegram kéo dài hình phạt)."""
        with patch.object(tb, "urlopen", side_effect=_rate_limited(30)), \
             patch.object(tb.time, "sleep"):
            tb._send_single_text("burst")
        with patch.object(tb, "urlopen", side_effect=[_ok_response()]), \
             patch.object(tb.time, "sleep") as slept:
            tb._send_single_text("sau đó")
        self.assertTrue(max(c.args[0] for c in slept.call_args_list) > 1.0)

    def test_getupdates_is_not_blocked_by_send_penalty(self):
        """Cửa sổ phạt của sendMessage không được làm bot ĐIẾC với lệnh user."""
        tb._note_rate_limit(60)
        with patch.object(tb.os.path, "exists", return_value=False), \
             patch.object(tb, "urlopen", side_effect=[_ok_response("[]")]), \
             patch.object(tb.time, "sleep") as slept:
            self.assertEqual(tb._get_updates(timeout=0), [])
        slept.assert_not_called()


class TestConflictBackoff(_TelegramTest):
    """409 dai dẳng (instance khác đang poll) không được nã API cả ngày —
    request đều đặn là thứ đẩy chính token vào 429 (issue #119)."""

    def _poll_409(self):
        with patch.object(tb.os.path, "exists", return_value=False), \
             patch.object(tb, "urlopen", side_effect=_http_error(409, "Conflict")), \
             patch.object(tb, "_delete_webhook") as dw, \
             patch.object(tb.time, "sleep") as slept:
            tb._get_updates(timeout=0)
        return dw.call_count, slept.call_args.args[0]

    def test_backoff_grows_and_stops_healing_webhook(self):
        waits = []
        heals = 0
        for _ in range(6):
            healed, wait = self._poll_409()
            heals += healed
            waits.append(wait)
        self.assertEqual(waits, [5, 10, 20, 40, 60, 60])   # lùi dần, có trần
        self.assertEqual(heals, tb._CONFLICT_HEAL_ATTEMPTS)  # thôi chữa vô ích

    def test_successful_poll_resets_backoff(self):
        self._poll_409()
        self._poll_409()
        with patch.object(tb.os.path, "exists", return_value=False), \
             patch.object(tb, "urlopen", side_effect=[_ok_response("[]")]):
            tb._get_updates(timeout=0)
        self.assertEqual(self._poll_409()[1], 5)


class TestNoDuplicateSideEffects(_TelegramTest):
    def test_video_upload_not_retried_on_network_error(self):
        """sendVideo timeout: không rõ file ~50MB đã tới hay chưa → KHÔNG gửi
        lại (Bé MC nhận video trùng còn tệ hơn mất một tin)."""
        with patch.object(tb.os.path, "getsize", return_value=1024), \
             patch("builtins.open", unittest.mock.mock_open(read_data=b"x")), \
             patch.object(tb, "urlopen", side_effect=TimeoutError("timed out")) as u, \
             patch.object(tb.time, "sleep"):
            self.assertIsNone(tb._send_video_file("/tmp/v.mp4", "cap"))
        self.assertEqual(u.call_count, 1)

    def test_video_upload_is_retried_on_429(self):
        """429 = Telegram TỪ CHỐI xử lý → gửi lại chắc chắn không tạo bản trùng."""
        with patch.object(tb.os.path, "getsize", return_value=1024), \
             patch("builtins.open", unittest.mock.mock_open(read_data=b"x")), \
             patch.object(tb, "urlopen",
                          side_effect=[_rate_limited(3), _ok_response()]) as u, \
             patch.object(tb.time, "sleep"):
            self.assertEqual(tb._send_video_file("/tmp/v.mp4", "cap"), "7")
        self.assertEqual(u.call_count, 2)


class TestLogHygiene(_TelegramTest):
    def test_bot_token_is_redacted_from_logs(self):
        """Log hay được dán vào issue khi debug — token lọt ra là mất bot."""
        self.assertNotIn("123:SECRET", tb._redact("lỗi ở https://api.telegram.org/bot123:SECRET/x"))
        self.assertIn("***", tb._redact("bot123:SECRET"))

    def test_getupdates_timeout_stays_warning(self):
        """Nhiễu long-poll bình thường không được tích rác ERROR (issue #107)."""
        with patch.object(tb.os.path, "exists", return_value=False), \
             patch.object(tb, "urlopen", side_effect=TimeoutError("read operation timed out")), \
             patch.object(tb.logger, "error") as error, \
             patch.object(tb.logger, "warning") as warning:
            self.assertEqual(tb._get_updates(timeout=0), [])
        error.assert_not_called()
        warning.assert_called()


if __name__ == "__main__":
    unittest.main()
