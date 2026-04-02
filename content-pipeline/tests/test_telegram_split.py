"""Tests for Telegram message splitting and send logic."""
from __future__ import annotations

import sys
import os
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from notifier.telegram_bot import _split_message, _send_text_chunks, send_pipeline_summary


class TestSplitMessage(unittest.TestCase):
    """Test _split_message helper."""

    def test_short_message_no_split(self):
        text = "Hello world"
        result = _split_message(text)
        self.assertEqual(result, [text])

    def test_exactly_4096_chars(self):
        text = "a" * 4096
        result = _split_message(text)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], text)

    def test_split_at_paragraph_boundary(self):
        # 2000 chars + \n\n + 2500 chars = 4502, exceeds 4096
        part1 = "A" * 2000
        part2 = "B" * 2500
        text = part1 + "\n\n" + part2
        result = _split_message(text)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], part1)
        self.assertEqual(result[1], part2)

    def test_split_at_newline_when_no_paragraph(self):
        # No double-newline, fall back to single newline
        part1 = "A" * 2000
        part2 = "B" * 2500
        text = part1 + "\n" + part2
        result = _split_message(text)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], part1)
        self.assertEqual(result[1], part2)

    def test_hard_cut_when_no_newlines(self):
        text = "A" * 5000  # No newlines at all
        result = _split_message(text, max_len=4096)
        self.assertEqual(len(result), 2)
        self.assertEqual(len(result[0]), 4096)
        self.assertEqual(len(result[1]), 904)

    def test_multiple_splits(self):
        # 3 chunks needed
        text = ("A" * 3000 + "\n\n") * 3
        result = _split_message(text.strip())
        self.assertGreaterEqual(len(result), 2)
        # Verify all content is preserved
        reassembled = "\n\n".join(result)
        self.assertEqual(len(reassembled), len(text.strip()))

    def test_empty_string(self):
        result = _split_message("")
        self.assertEqual(result, [""])

    def test_custom_max_len(self):
        text = "Hello\n\nWorld"
        result = _split_message(text, max_len=7)
        self.assertEqual(result, ["Hello", "World"])


class TestSendTextChunks(unittest.TestCase):
    """Test _send_text_chunks with mocked Telegram API."""

    @patch("notifier.telegram_bot._send_single_text")
    @patch("notifier.telegram_bot.config")
    def test_short_message_single_call(self, mock_config, mock_send):
        mock_config.TELEGRAM_BOT_TOKEN = "token"
        mock_config.TELEGRAM_CHAT_ID = "123"
        mock_send.return_value = True

        result = _send_text_chunks("Hello world")
        self.assertTrue(result)
        mock_send.assert_called_once()
        # No [n/total] marker for single message
        call_text = mock_send.call_args[0][0]
        self.assertNotIn("[1/", call_text)

    @patch("notifier.telegram_bot._send_single_text")
    @patch("notifier.telegram_bot.config")
    def test_long_message_adds_markers(self, mock_config, mock_send):
        mock_config.TELEGRAM_BOT_TOKEN = "token"
        mock_config.TELEGRAM_CHAT_ID = "123"
        mock_send.return_value = True

        text = "A" * 3000 + "\n\n" + "B" * 3000
        result = _send_text_chunks(text)
        self.assertTrue(result)
        self.assertEqual(mock_send.call_count, 2)

        # First chunk should have [1/2] marker
        first_call = mock_send.call_args_list[0][0][0]
        self.assertIn("[1/2]", first_call)

        # Second chunk should have [2/2] marker
        second_call = mock_send.call_args_list[1][0][0]
        self.assertIn("[2/2]", second_call)

    @patch("notifier.telegram_bot._send_single_text")
    @patch("notifier.telegram_bot.config")
    def test_partial_failure(self, mock_config, mock_send):
        mock_config.TELEGRAM_BOT_TOKEN = "token"
        mock_config.TELEGRAM_CHAT_ID = "123"
        mock_send.side_effect = [True, False]  # First succeeds, second fails

        text = "A" * 3000 + "\n\n" + "B" * 3000
        result = _send_text_chunks(text)
        self.assertFalse(result)

    @patch("notifier.telegram_bot._send_single_text")
    @patch("notifier.telegram_bot.config")
    def test_no_credentials(self, mock_config, mock_send):
        mock_config.TELEGRAM_BOT_TOKEN = ""
        mock_config.TELEGRAM_CHAT_ID = ""

        result = _send_text_chunks("Hello")
        self.assertFalse(result)
        mock_send.assert_not_called()


class TestSendPipelineSummary(unittest.TestCase):
    """Test send_pipeline_summary with long error lists."""

    @patch("notifier.telegram_bot._send_text_chunks")
    @patch("notifier.telegram_bot.get_videos_by_status")
    def test_summary_with_errors_is_sent_fully(self, mock_videos, mock_send):
        mock_videos.return_value = []
        mock_send.return_value = True

        errors = [f"Error {i}: " + "x" * 200 for i in range(5)]
        send_pipeline_summary(0, 0, errors)

        mock_send.assert_called_once()
        sent_text = mock_send.call_args[0][0]
        # All errors should be in the message (no truncation of error list)
        for i in range(5):
            self.assertIn(f"Error {i}", sent_text)


if __name__ == "__main__":
    unittest.main()
