"""Tests for send_video_for_approval status handling (issue #60).

The video file upload is size-limited and flaky; the *script text* is the real
review artifact. These tests pin down that the pending_approval transition is
driven by reviewability (script OR video delivered), not by the video upload
alone — so a failed/oversized video upload never strands a video at status=ready.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import notifier.telegram_bot as tb


def _video(**over):
    base = {
        "id": 97,
        "video_type": "long",
        "video_path": "/tmp/video_97.mp4",
        "script_text": "Xin chào, đây là script thật.",
        "youtube_title": "AI trong 5 phút",
        "scheduled_platform": "youtube",
        "scheduled_date": "2026-06-27",
    }
    base.update(over)
    return base


class TestSendVideoForApproval(unittest.TestCase):
    def setUp(self):
        # Common patches: valid creds, existing file, no-op telegram_id write.
        self.p_cfg = patch.object(tb, "config")
        self.cfg = self.p_cfg.start()
        self.cfg.TELEGRAM_BOT_TOKEN = "token"
        self.cfg.TELEGRAM_CHAT_ID = "123"
        self.cfg.ENABLE_BGM = False
        self.addCleanup(self.p_cfg.stop)

        self.p_exists = patch.object(tb.os.path, "exists", return_value=True)
        self.p_exists.start()
        self.addCleanup(self.p_exists.stop)

        self.p_tgid = patch.object(tb, "update_video_telegram_id")
        self.p_tgid.start()
        self.addCleanup(self.p_tgid.stop)

        # Phase 5: preview compression chạy trước khi gửi — pass-through để
        # các test này tiếp tục pin hành vi reviewability, không test nén.
        self.p_prev = patch("video.preview.compress_for_preview",
                            side_effect=lambda p: p)
        self.p_prev.start()
        self.addCleanup(self.p_prev.stop)

    def test_video_upload_fails_but_script_sent_sets_pending(self):
        """Core issue #60: video upload fails, script ok -> pending_approval."""
        with patch.object(tb, "get_video", return_value=_video()), \
             patch.object(tb, "_send_text", return_value=True) as send_text, \
             patch.object(tb, "_send_video_file", return_value=None), \
             patch.object(tb, "update_video_status") as upd:
            result = tb.send_video_for_approval(97)

        self.assertTrue(result)
        upd.assert_called_once_with(97, "pending_approval")
        # Reviewer is notified the video file could not be delivered.
        notice_sent = any("Không gửi được FILE VIDEO" in c.args[0]
                          for c in send_text.call_args_list)
        self.assertTrue(notice_sent)

    def test_video_upload_succeeds_sets_pending_no_notice(self):
        with patch.object(tb, "get_video", return_value=_video()), \
             patch.object(tb, "_send_text", return_value=True) as send_text, \
             patch.object(tb, "_send_video_file", return_value="555") as sendvid, \
             patch.object(tb, "update_video_status") as upd:
            result = tb.send_video_for_approval(97)

        self.assertTrue(result)
        upd.assert_called_once_with(97, "pending_approval")
        sendvid.assert_called_once()
        # No failure notice when the video went through.
        self.assertFalse(any("Không gửi được FILE VIDEO" in c.args[0]
                             for c in send_text.call_args_list))

    def test_no_script_but_video_sent_sets_pending(self):
        with patch.object(tb, "get_video", return_value=_video(script_text="")), \
             patch.object(tb, "_send_text", return_value=True), \
             patch.object(tb, "_send_video_file", return_value="555"), \
             patch.object(tb, "update_video_status") as upd:
            result = tb.send_video_for_approval(97)

        self.assertTrue(result)
        upd.assert_called_once_with(97, "pending_approval")

    def test_both_fail_stays_ready_returns_false(self):
        """If neither artifact reaches the reviewer, do NOT mark pending."""
        with patch.object(tb, "get_video", return_value=_video()), \
             patch.object(tb, "_send_text", return_value=False), \
             patch.object(tb, "_send_video_file", return_value=None), \
             patch.object(tb, "update_video_status") as upd:
            result = tb.send_video_for_approval(97)

        self.assertFalse(result)
        upd.assert_not_called()

    def test_missing_video_returns_false(self):
        with patch.object(tb, "get_video", return_value=None), \
             patch.object(tb, "update_video_status") as upd:
            result = tb.send_video_for_approval(97)
        self.assertFalse(result)
        upd.assert_not_called()

    def test_missing_file_returns_false_stays_ready(self):
        self.p_exists.stop()  # make os.path.exists return real (False) for /tmp path
        with patch.object(tb.os.path, "exists", return_value=False), \
             patch.object(tb, "get_video", return_value=_video()), \
             patch.object(tb, "update_video_status") as upd:
            result = tb.send_video_for_approval(97)
        self.assertFalse(result)
        upd.assert_not_called()


class TestSendVideoFileSizeGuard(unittest.TestCase):
    @patch.object(tb, "config")
    def test_oversized_file_skipped(self, cfg):
        cfg.TELEGRAM_BOT_TOKEN = "token"
        cfg.TELEGRAM_CHAT_ID = "123"
        with patch.object(tb.os.path, "getsize",
                          return_value=tb.TELEGRAM_MAX_FILE_BYTES + 1), \
             patch.object(tb, "urlopen") as urlopen:
            result = tb._send_video_file("/tmp/big.mp4", "caption")
        self.assertIsNone(result)
        urlopen.assert_not_called()  # never attempted the doomed upload

    @patch.object(tb, "config")
    def test_unstattable_file_returns_none(self, cfg):
        cfg.TELEGRAM_BOT_TOKEN = "token"
        cfg.TELEGRAM_CHAT_ID = "123"
        with patch.object(tb.os.path, "getsize", side_effect=OSError("nope")), \
             patch.object(tb, "urlopen") as urlopen:
            result = tb._send_video_file("/tmp/gone.mp4", "caption")
        self.assertIsNone(result)
        urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
