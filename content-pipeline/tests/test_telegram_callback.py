"""Tests for review-gate callback handling + long-poll resilience (issue #88).

Ba lỗi khiến nút "Duyệt" trên Telegram không hoạt động:
  1. answerCallbackQuery được gọi SAU bước xử lý nặng (approve) → callback_id
     hết hạn → HTTP 400 "query is too old"; nút kẹt xoay, log đầy ERROR.
  2. Webhook tồn đọng khiến MỌI getUpdates trả 409 Conflict; code cũ nuốt 409
     nên run_bot busy-loop nã API.
  3. HTTPError bị str() giấu mất `description` thật nên không debug được.

Các test dưới pin hành vi đã sửa: ack TRƯỚC khi xử lý, 400 là info (không phải
ERROR), 409 tự gọi deleteWebhook + không raise.
"""
from __future__ import annotations

import io
import os
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import notifier.telegram_bot as tb


def _http_error(code: int, description: str) -> HTTPError:
    body = ('{"ok":false,"error_code":%d,"description":"%s"}' % (code, description))
    return HTTPError(
        url="https://api.telegram.org/botX/method",
        code=code,
        msg="Bad Request" if code == 400 else "Conflict",
        hdrs=None,
        fp=io.BytesIO(body.encode("utf-8")),
    )


class TestCallbackAckOrdering(unittest.TestCase):
    """Ack callback phải chạy TRƯỚC review_bot.handle_callback (issue #88)."""

    def setUp(self):
        self.p_cfg = patch.object(tb, "config")
        self.cfg = self.p_cfg.start()
        self.cfg.TELEGRAM_BOT_TOKEN = "token"
        self.cfg.TELEGRAM_CHAT_ID = "123"
        self.addCleanup(self.p_cfg.stop)

    def test_ack_before_heavy_processing(self):
        order = []

        def _handle(data):
            order.append("handle")
            return ("✅ ok", None)

        with patch.object(tb, "_answer_callback_query",
                          side_effect=lambda cid, *a, **k: order.append("ack")), \
             patch("notifier.review_bot.handle_callback", side_effect=_handle), \
             patch.object(tb, "_send_text") as send:
            tb._handle_callback_query({
                "id": "cb1",
                "message": {"chat": {"id": "123"}},
                "data": "rv:a:117",
            })

        self.assertEqual(order, ["ack", "handle"],
                         "ack phải chạy trước handle_callback (tránh 400 query too old)")
        send.assert_called_once()  # kết quả gửi bằng message riêng

    def test_wrong_chat_acked_but_not_processed(self):
        with patch.object(tb, "_answer_callback_query") as ack, \
             patch("notifier.review_bot.handle_callback") as handle:
            tb._handle_callback_query({
                "id": "cb2",
                "message": {"chat": {"id": "999"}},  # sai chat
                "data": "rv:a:117",
            })
        ack.assert_called_once_with("cb2")           # vẫn nhả nút của người lạ
        handle.assert_not_called()


class TestAnswerCallbackErrorHandling(unittest.TestCase):
    def setUp(self):
        self.p_cfg = patch.object(tb, "config")
        self.cfg = self.p_cfg.start()
        self.cfg.TELEGRAM_BOT_TOKEN = "token"
        self.addCleanup(self.p_cfg.stop)

    def test_400_query_too_old_is_info_not_error(self):
        err = _http_error(400, "Bad Request: query is too old and response timeout expired")
        with patch.object(tb, "urlopen", side_effect=err), \
             patch.object(tb.logger, "info") as info, \
             patch.object(tb.logger, "error") as error:
            ok = tb._answer_callback_query("cb1")
        self.assertFalse(ok)
        error.assert_not_called()                    # 400 KHÔNG phải ERROR
        self.assertTrue(info.called)
        # description thật + callback_id có mặt để debug (gợi ý #3 của issue)
        logged = " ".join(str(a) for a in info.call_args.args)
        self.assertIn("cb1", logged)
        self.assertIn("query is too old", logged)

    def test_non_400_still_logs_error_with_body(self):
        err = _http_error(500, "Internal Server Error")
        with patch.object(tb, "urlopen", side_effect=err), \
             patch.object(tb.logger, "error") as error:
            ok = tb._answer_callback_query("cb9")
        self.assertFalse(ok)
        error.assert_called_once()
        logged = " ".join(str(a) for a in error.call_args.args)
        self.assertIn("cb9", logged)


class TestGetUpdatesConflict(unittest.TestCase):
    def setUp(self):
        self.p_cfg = patch.object(tb, "config")
        self.cfg = self.p_cfg.start()
        self.cfg.TELEGRAM_BOT_TOKEN = "token"
        self.addCleanup(self.p_cfg.stop)
        # Không đọc offset file thật
        self.p_exists = patch.object(tb.os.path, "exists", return_value=False)
        self.p_exists.start()
        self.addCleanup(self.p_exists.stop)

    def test_409_self_heals_via_delete_webhook_without_raising(self):
        err = _http_error(409, "Conflict: terminated by other getUpdates request")
        with patch.object(tb, "urlopen", side_effect=err), \
             patch.object(tb, "_delete_webhook") as dw, \
             patch.object(tb.time, "sleep") as slept:
            result = tb._get_updates(timeout=0)
        self.assertEqual(result, [])                 # nuốt gọn, không raise
        dw.assert_called_once()                      # tự chữa webhook
        slept.assert_called_once()                   # lùi, không busy-loop


if __name__ == "__main__":
    unittest.main()
