"""Tests for the channel registry (Phase 1 — Multi-channel Foundation)."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import channels


class TestChannelRegistry(unittest.TestCase):
    def test_has_at_least_three_channels(self):
        self.assertGreaterEqual(len(channels.CHANNELS), 3)

    def test_expected_channels_present(self):
        for key in ("ai_youtube", "drama_youtube", "tiktok_main"):
            self.assertIn(key, channels.CHANNELS)

    def test_each_channel_has_required_fields(self):
        required = {
            "platform", "track", "name", "format_long",
            "format_shorts", "oauth_token_env", "tts_voice_profile",
        }
        for key, channel in channels.CHANNELS.items():
            missing = required - channel.keys()
            self.assertFalse(missing, f"{key} missing fields: {missing}")

    def test_default_track_is_ai_for_ai_youtube(self):
        self.assertEqual(channels.get_channel("ai_youtube")["track"], "ai")


class TestGetChannel(unittest.TestCase):
    def test_get_channel_returns_dict(self):
        channel = channels.get_channel("ai_youtube")
        self.assertEqual(channel["platform"], "youtube")

    def test_get_channel_raises_on_unknown_key(self):
        with self.assertRaises(ValueError):
            channels.get_channel("does_not_exist")


class TestChannelsForTrack(unittest.TestCase):
    def test_ai_track_includes_mixed_channel(self):
        result = channels.channels_for_track("ai")
        self.assertIn("ai_youtube", result)
        self.assertIn("tiktok_main", result)
        self.assertNotIn("drama_youtube", result)

    def test_drama_track_includes_mixed_channel(self):
        result = channels.channels_for_track("drama")
        self.assertIn("drama_youtube", result)
        self.assertIn("tiktok_main", result)
        self.assertNotIn("ai_youtube", result)


class TestChannelsForPlatform(unittest.TestCase):
    def test_youtube_platform_has_two_channels(self):
        result = channels.channels_for_platform("youtube")
        self.assertEqual(set(result.keys()), {"ai_youtube", "drama_youtube"})

    def test_tiktok_platform_has_one_channel(self):
        result = channels.channels_for_platform("tiktok")
        self.assertEqual(set(result.keys()), {"tiktok_main"})


if __name__ == "__main__":
    unittest.main()
