"""Tests for telegram_bot.send_tiktok_manual — TikTok = gửi video qua Telegram
(kênh Bé MC) để upload tay (không auto-upload)."""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import notifier.telegram_bot as tb


def _video(**over):
    base = {
        "id": 42,
        "video_path": "/tmp/video_42.mp4",
        "youtube_title": "Tiêu đề",
        "tiktok_caption": "caption ngắn",
        "tiktok_hashtags": "#AI #VN",
    }
    base.update(over)
    return base


class TestSendTiktokManual(unittest.TestCase):
    def setUp(self):
        self.p_cfg = patch.object(tb, "config")
        self.cfg = self.p_cfg.start()
        self.addCleanup(self.p_cfg.stop)
        self.cfg.TELEGRAM_BOT_TOKEN = "token"
        self.cfg.TELEGRAM_CHAT_ID = "main_chat"
        self.cfg.TELEGRAM_TIKTOK_CHAT_ID = "be_mc_chat"

        self.p_get = patch.object(tb, "get_video", return_value=_video())
        self.p_get.start()
        self.addCleanup(self.p_get.stop)
        self.p_exists = patch("os.path.exists", return_value=True)
        self.p_exists.start()
        self.addCleanup(self.p_exists.stop)

    def test_small_file_sent_original_to_be_mc(self):
        with patch("os.path.getsize", return_value=10 * 1024 * 1024), \
             patch.object(tb, "_send_video_file", return_value="55") as sendv:
            ok = tb.send_tiktok_manual(42)
        self.assertTrue(ok)
        args, kwargs = sendv.call_args
        # gửi FILE GỐC (không nén) tới đúng chat Bé MC
        self.assertEqual(args[0], "/tmp/video_42.mp4")
        self.assertEqual(kwargs["chat_id"], "be_mc_chat")
        # caption chứa hashtags + hướng dẫn upload tay
        self.assertIn("#AI #VN", args[1])
        self.assertIn("upload", args[1].lower())

    def test_falls_back_to_main_chat_when_be_mc_unset(self):
        self.cfg.TELEGRAM_TIKTOK_CHAT_ID = ""
        with patch("os.path.getsize", return_value=1024), \
             patch.object(tb, "_send_video_file", return_value="1") as sendv:
            tb.send_tiktok_manual(42)
        self.assertEqual(sendv.call_args.kwargs["chat_id"], "main_chat")

    def test_oversized_file_falls_back_to_queue_and_text(self):
        big = tb.TELEGRAM_MAX_FILE_BYTES + 1
        with patch("os.path.getsize", return_value=big), \
             patch.object(tb, "_send_video_file") as sendv, \
             patch("publisher.tiktok_manual.export_for_manual_upload",
                   return_value="/queue/video_42.mp4") as exp, \
             patch.object(tb, "_send_single_text", return_value=True) as sendt:
            ok = tb.send_tiktok_manual(42)
        self.assertTrue(ok)
        sendv.assert_not_called()          # không cố gửi file quá cỡ
        exp.assert_called_once_with(42)    # giữ bản gốc trong queue tay
        note = sendt.call_args.args[0]
        self.assertIn("/queue/video_42.mp4", note)
        self.assertEqual(sendt.call_args.kwargs["chat_id"], "be_mc_chat")

    def test_send_video_failure_falls_back_to_text(self):
        with patch("os.path.getsize", return_value=1024), \
             patch.object(tb, "_send_video_file", return_value=None), \
             patch("publisher.tiktok_manual.export_for_manual_upload",
                   return_value=None), \
             patch.object(tb, "_send_single_text", return_value=True) as sendt:
            ok = tb.send_tiktok_manual(42)
        self.assertTrue(ok)
        sendt.assert_called_once()

    def test_no_video_returns_false(self):
        with patch.object(tb, "get_video", return_value=None):
            self.assertFalse(tb.send_tiktok_manual(999))

    def test_no_chat_configured_returns_false(self):
        self.cfg.TELEGRAM_CHAT_ID = ""
        self.cfg.TELEGRAM_TIKTOK_CHAT_ID = ""
        self.assertFalse(tb.send_tiktok_manual(42))

    def test_missing_file_returns_false(self):
        with patch("os.path.exists", return_value=False):
            self.assertFalse(tb.send_tiktok_manual(42))


if __name__ == "__main__":
    unittest.main()
