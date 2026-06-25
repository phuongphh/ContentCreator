"""Tests for video.audio_mixer (Phase 1 / V1.3 background music)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import video.audio_mixer as mixer
from video.audio_mixer import build_mix_command, pick_music, mix_background_music


class TestBuildMixCommand(unittest.TestCase):
    def test_has_ducking_and_amix(self):
        cmd = build_mix_command("voice.mp3", "music.mp3", "out.mp3", -18.0)
        fc = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("sidechaincompress", fc)  # ducking
        self.assertIn("amix", fc)

    def test_music_volume_applied(self):
        cmd = build_mix_command("voice.mp3", "music.mp3", "out.mp3", -12.0)
        fc = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("volume=-12.0dB", fc)

    def test_voice_is_first_input(self):
        cmd = build_mix_command("voice.mp3", "music.mp3", "out.mp3")
        first_i = cmd.index("-i")
        self.assertEqual(cmd[first_i + 1], "voice.mp3")

    def test_output_length_follows_voice(self):
        cmd = build_mix_command("voice.mp3", "music.mp3", "out.mp3")
        self.assertIn("-shortest", cmd)
        self.assertEqual(cmd[-1], "out.mp3")

    def test_music_is_looped(self):
        cmd = build_mix_command("voice.mp3", "music.mp3", "out.mp3")
        self.assertIn("-stream_loop", cmd)


class TestPickMusic(unittest.TestCase):
    def test_no_dir_returns_none(self):
        with patch.object(mixer.config, "MUSIC_DIR", "/no/such/dir"):
            self.assertIsNone(pick_music())

    def test_empty_dir_returns_none(self):
        tmp = tempfile.mkdtemp()
        self.assertIsNone(pick_music(tmp))

    def test_ignores_non_audio(self):
        tmp = tempfile.mkdtemp()
        open(os.path.join(tmp, "CREDITS.md"), "w").close()
        open(os.path.join(tmp, ".gitkeep"), "w").close()
        self.assertIsNone(pick_music(tmp))

    def test_picks_audio_file(self):
        tmp = tempfile.mkdtemp()
        track = os.path.join(tmp, "calm.mp3")
        open(track, "w").close()
        self.assertEqual(pick_music(tmp), track)


class TestMixBackgroundMusic(unittest.TestCase):
    def test_no_music_returns_voice(self):
        with patch.object(mixer, "pick_music", return_value=None):
            self.assertEqual(
                mix_background_music("voice.mp3", "out.mp3"), "voice.mp3"
            )

    def test_ffmpeg_failure_returns_voice(self):
        tmp = tempfile.mkdtemp()
        track = os.path.join(tmp, "m.mp3")
        open(track, "w").close()
        with patch.object(mixer, "_run_ffmpeg", return_value=None):
            out = mix_background_music("voice.mp3", "out.mp3", music_path=track)
        self.assertEqual(out, "voice.mp3")

    def test_success_returns_output(self):
        tmp = tempfile.mkdtemp()
        track = os.path.join(tmp, "m.mp3")
        open(track, "w").close()
        with patch.object(mixer, "_run_ffmpeg", return_value="out.mp3"):
            out = mix_background_music("voice.mp3", "out.mp3", music_path=track)
        self.assertEqual(out, "out.mp3")


if __name__ == "__main__":
    unittest.main()
