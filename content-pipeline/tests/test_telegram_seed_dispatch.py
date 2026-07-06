"""Tests for the Drama seed bot commands wired into telegram_bot._handle_update
(Phase 2 — Drama Source Layer).

Verifies the dispatch/plumbing only — notifier/seed_bot.py's own logic
(saving stories, OG scraping, ...) is covered by tests/test_seed_bot.py.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import notifier.telegram_bot as tb
import notifier.seed_bot as seed_bot


def _update(text: str, chat_id: str = "123"):
    return {"message": {"text": text, "chat": {"id": chat_id}}}


class TestSeedCommandDispatch(unittest.TestCase):
    def setUp(self):
        self.p_cfg = patch.object(tb, "config")
        self.cfg = self.p_cfg.start()
        self.cfg.TELEGRAM_BOT_TOKEN = "token"
        self.cfg.TELEGRAM_CHAT_ID = "123"
        self.addCleanup(self.p_cfg.stop)

    def test_seed_vn_command_calls_start_seed_vn(self):
        with patch.object(seed_bot, "start_seed_vn", return_value="prompt text") as start, \
             patch.object(tb, "_send_text") as send_text:
            tb._handle_update(_update("/seed_vn"), publish_callback=None)
        start.assert_called_once()
        send_text.assert_called_once_with("prompt text")

    def test_seed_url_command_calls_start_seed_url(self):
        with patch.object(seed_bot, "start_seed_url", return_value="prompt url") as start, \
             patch.object(tb, "_send_text") as send_text:
            tb._handle_update(_update("/seed_url"), publish_callback=None)
        start.assert_called_once()
        send_text.assert_called_once_with("prompt url")

    def test_list_pending_command_calls_list_pending_text(self):
        with patch.object(seed_bot, "list_pending_text", return_value="list here") as lp, \
             patch.object(tb, "_send_text") as send_text:
            tb._handle_update(_update("/list_pending"), publish_callback=None)
        lp.assert_called_once()
        send_text.assert_called_once_with("list here")

    def test_help_command_includes_seed_bot_help(self):
        with patch.object(seed_bot, "help_text", return_value="SEED HELP TEXT") as ht, \
             patch.object(tb, "_send_text") as send_text:
            tb._handle_update(_update("/help"), publish_callback=None)
        ht.assert_called_once()
        sent = send_text.call_args[0][0]
        self.assertIn("SEED HELP TEXT", sent)
        self.assertIn("/approve_<id>", sent)  # existing video-approval help untouched

    def test_plain_text_with_awaiting_state_is_consumed(self):
        with patch.object(seed_bot, "handle_awaiting_message", return_value="saved!") as haw, \
             patch.object(tb, "_send_text") as send_text:
            tb._handle_update(_update("Sếp bắt làm thêm giờ."), publish_callback=None)
        haw.assert_called_once_with("Sếp bắt làm thêm giờ.")
        send_text.assert_called_once_with("saved!")

    def test_plain_text_without_awaiting_state_sends_nothing(self):
        with patch.object(seed_bot, "handle_awaiting_message", return_value=None), \
             patch.object(tb, "_send_text") as send_text:
            tb._handle_update(_update("just chatting"), publish_callback=None)
        send_text.assert_not_called()

    def test_wrong_chat_id_ignored(self):
        with patch.object(seed_bot, "handle_awaiting_message") as haw, \
             patch.object(tb, "_send_text") as send_text:
            tb._handle_update(_update("/seed_vn", chat_id="999"), publish_callback=None)
        haw.assert_not_called()
        send_text.assert_not_called()

    def test_approve_command_still_works_mid_seed_conversation(self):
        # /approve_<id> must dispatch normally even if a seed prompt is
        # technically "awaiting" — commands always take priority.
        with patch.object(seed_bot, "handle_awaiting_message") as haw, \
             patch("video.review_service.approve", return_value=(True, "ok")) as approve, \
             patch.object(tb, "_send_text"):
            tb._handle_update(_update("/approve_5"), publish_callback=lambda vid: None)
        haw.assert_not_called()
        approve.assert_called_once()


if __name__ == "__main__":
    unittest.main()
