"""Tests for video/drama_composer.py (Phase 4 EPIC #4.2 — scene reel builder).

Command-building/pure-logic functions are tested with mocks. A skippable
real-ffmpeg smoke test exercises the full build_drama_scene_reel() pipeline
end-to-end (concat + overlay) when ffmpeg is actually installed.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from video.drama_composer import (
    _lavfi_source,
    _resolve_scene_background,
    _write_scene_concat_playlist,
    build_scene_concat_command,
    build_scene_segment_command,
    build_drama_scene_reel,
    scaled_scene_durations,
    compose_drama_video,
)

HAS_FFMPEG = shutil.which("ffmpeg") is not None
HAS_FFPROBE = shutil.which("ffprobe") is not None


class TestLavfiSource(unittest.TestCase):
    def test_gradient_spec(self):
        src = _lavfi_source("gradient_warm", 1080, 1920)
        self.assertTrue(src.startswith("gradients=s=1080x1920:"))
        self.assertIn("c0=0xFF6B35", src)
        self.assertIn("c1=0xF7C59F", src)

    def test_solid_spec(self):
        src = _lavfi_source("solid_blue", 1080, 1920)
        self.assertEqual(src, "color=c=0x1B3A5C:s=1080x1920")

    def test_unknown_returns_none(self):
        self.assertIsNone(_lavfi_source("illustration", 1080, 1920))
        self.assertIsNone(_lavfi_source("screen_record", 1080, 1920))


class TestBuildSceneSegmentCommand(unittest.TestCase):
    def test_lavfi_no_overlay(self):
        cmd = build_scene_segment_command(
            "color=c=0x000000:s=1080x1920", is_lavfi=True, duration=3.0,
            width=1080, height=1920, output_path="/tmp/seg0.mp4",
        )
        self.assertEqual(cmd[:4], ["ffmpeg", "-y", "-f", "lavfi"])
        self.assertIn("-i", cmd)
        self.assertIn("color=c=0x000000:s=1080x1920:d=3.0", cmd)
        self.assertIn("-map", cmd)
        self.assertIn("0:v", cmd)
        self.assertNotIn("-filter_complex", cmd)
        self.assertIn("/tmp/seg0.mp4", cmd)

    def test_file_source_no_overlay(self):
        cmd = build_scene_segment_command(
            "/tmp/illustration.png", is_lavfi=False, duration=5.0,
            width=1080, height=1920, output_path="/tmp/seg1.mp4",
        )
        self.assertIn("-stream_loop", cmd)
        self.assertIn("-1", cmd)
        self.assertIn("/tmp/illustration.png", cmd)

    def test_with_overlay_uses_filter_complex(self):
        cmd = build_scene_segment_command(
            "color=c=0x000000:s=1080x1920", is_lavfi=True, duration=3.0,
            width=1080, height=1920, output_path="/tmp/seg0.mp4",
            overlay_png="/tmp/overlay.png",
        )
        self.assertIn("-filter_complex", cmd)
        idx = cmd.index("-filter_complex")
        self.assertIn("overlay=shortest=0", cmd[idx + 1])
        self.assertIn("[v]", cmd)
        self.assertIn("/tmp/overlay.png", cmd)

    def test_output_codec_settings(self):
        cmd = build_scene_segment_command(
            "color=c=0x000000:s=1080x1920", is_lavfi=True, duration=3.0,
            width=1080, height=1920, output_path="/tmp/seg0.mp4",
        )
        self.assertIn("libx264", cmd)
        self.assertIn("yuv420p", cmd)
        self.assertIn("-t", cmd)
        self.assertIn("3.0", cmd)


class TestBuildSceneConcatCommand(unittest.TestCase):
    def test_stream_copy_concat(self):
        cmd = build_scene_concat_command("/tmp/scenes.concat", "/tmp/reel.mp4")
        self.assertEqual(
            cmd,
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "/tmp/scenes.concat",
             "-c", "copy", "/tmp/reel.mp4"],
        )


class TestWriteSceneConcatPlaylist(unittest.TestCase):
    def test_writes_one_line_per_segment(self):
        tmp = tempfile.mkdtemp()
        concat_path = os.path.join(tmp, "scenes.concat")
        _write_scene_concat_playlist(["/tmp/a.mp4", "/tmp/b.mp4"], concat_path)
        with open(concat_path, encoding="utf-8") as f:
            content = f.read()
        self.assertEqual(content, "file '/tmp/a.mp4'\nfile '/tmp/b.mp4'\n")


class TestScaledSceneDurations(unittest.TestCase):
    def test_scales_proportionally(self):
        template = {
            "duration_target": 90,
            "scenes": [{"duration": 3}, {"duration": 12}, {"duration": 75}],
        }
        durations = scaled_scene_durations(template, 45.0)
        self.assertAlmostEqual(sum(durations), 45.0, places=5)
        self.assertAlmostEqual(durations[0], 1.5)
        self.assertAlmostEqual(durations[1], 6.0)
        self.assertAlmostEqual(durations[2], 37.5)

    def test_falls_back_to_scene_sum_when_no_target(self):
        template = {"scenes": [{"duration": 1}, {"duration": 1}]}
        durations = scaled_scene_durations(template, 10.0)
        self.assertAlmostEqual(sum(durations), 10.0, places=5)

    def test_minimum_duration_floor(self):
        template = {"duration_target": 1000, "scenes": [{"duration": 1}, {"duration": 999}]}
        durations = scaled_scene_durations(template, 0.001)
        self.assertGreaterEqual(durations[0], 0.1)


class TestResolveSceneBackground(unittest.TestCase):
    def test_gradient_resolves_lavfi(self):
        scene = {"background": "gradient_warm"}
        source, is_lavfi = _resolve_scene_background(scene, 1080, 1920, 0, None)
        self.assertTrue(is_lavfi)
        self.assertIn("gradients=", source)

    @patch("video.drama_composer.generate_illustration")
    def test_illustration_success(self, mock_gen):
        mock_gen.return_value = "/tmp/illustration_0.png"
        scene = {"background": "illustration"}
        source, is_lavfi = _resolve_scene_background(scene, 1080, 1920, 0, "a lonely office")
        self.assertFalse(is_lavfi)
        self.assertEqual(source, "/tmp/illustration_0.png")
        mock_gen.assert_called_once_with("a lonely office", index=0)

    @patch("video.drama_composer.generate_illustration")
    def test_illustration_failure_falls_back(self, mock_gen):
        mock_gen.return_value = None
        scene = {"background": "illustration_dark"}
        source, is_lavfi = _resolve_scene_background(scene, 1080, 1920, 1, "a prompt")
        self.assertTrue(is_lavfi)
        self.assertIn("color=c=0x1B3A5C", source)

    def test_illustration_without_prompt_falls_back(self):
        scene = {"background": "illustration"}
        source, is_lavfi = _resolve_scene_background(scene, 1080, 1920, 0, None)
        self.assertTrue(is_lavfi)
        self.assertIn("color=c=0x1B3A5C", source)

    def test_unknown_background_falls_back(self):
        scene = {"background": "screen_record"}
        source, is_lavfi = _resolve_scene_background(scene, 1080, 1920, 0, None)
        self.assertTrue(is_lavfi)
        self.assertIn("color=c=0x1B3A5C", source)


class TestBuildDramaSceneReelMocked(unittest.TestCase):
    """Exercise the orchestration logic with ffmpeg calls stubbed out."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.template = {
            "duration_target": 10,
            "scenes": [
                {"type": "hook", "duration": 5, "background": "gradient_warm",
                 "lower_third": False, "commentary": False},
                {"type": "cta", "duration": 5, "background": "solid_blue",
                 "lower_third": False, "commentary": False},
            ],
        }

    @patch("video.drama_composer._run_ffmpeg")
    def test_returns_reel_path_on_success(self, mock_run):
        mock_run.side_effect = lambda cmd, out: out
        reel = build_drama_scene_reel(self.template, 10.0, 1080, 1920, self.tmp)
        self.assertEqual(reel, os.path.join(self.tmp, "scene_reel.mp4"))
        self.assertEqual(mock_run.call_count, 3)  # 2 segments + 1 concat

    @patch("video.drama_composer._run_ffmpeg")
    def test_segment_failure_returns_none(self, mock_run):
        mock_run.return_value = None
        reel = build_drama_scene_reel(self.template, 10.0, 1080, 1920, self.tmp)
        self.assertIsNone(reel)

    @patch("video.drama_composer._run_ffmpeg")
    def test_concat_failure_returns_none(self, mock_run):
        def side_effect(cmd, out):
            return None if "scene_reel.mp4" in out else out
        mock_run.side_effect = side_effect
        reel = build_drama_scene_reel(self.template, 10.0, 1080, 1920, self.tmp)
        self.assertIsNone(reel)

    @patch("video.drama_composer.render_lower_third")
    @patch("video.drama_composer._run_ffmpeg")
    def test_lower_third_only_rendered_when_scene_wants_it_and_data_given(self, mock_run, mock_lt):
        mock_run.side_effect = lambda cmd, out: out
        mock_lt.return_value = "/tmp/overlay.png"
        template = {
            "duration_target": 10,
            "scenes": [
                {"type": "escalation", "duration": 10, "background": "solid_blue",
                 "lower_third": True, "commentary": False},
            ],
        }
        build_drama_scene_reel(template, 10.0, 1080, 1920, self.tmp,
                               lower_third={"name": "Minh", "role": "Nhân vật chính"})
        mock_lt.assert_called_once()

    @patch("video.drama_composer.render_lower_third")
    @patch("video.drama_composer._run_ffmpeg")
    def test_lower_third_skipped_without_data(self, mock_run, mock_lt):
        mock_run.side_effect = lambda cmd, out: out
        template = {
            "duration_target": 10,
            "scenes": [
                {"type": "escalation", "duration": 10, "background": "solid_blue",
                 "lower_third": True, "commentary": False},
            ],
        }
        build_drama_scene_reel(template, 10.0, 1080, 1920, self.tmp, lower_third=None)
        mock_lt.assert_not_called()

    @patch("video.drama_composer.render_commentary_card")
    @patch("video.drama_composer._run_ffmpeg")
    def test_commentary_rendered_when_scene_wants_it_and_text_given(self, mock_run, mock_card):
        mock_run.side_effect = lambda cmd, out: out
        mock_card.return_value = "/tmp/card.png"
        template = {
            "duration_target": 10,
            "scenes": [
                {"type": "vn_commentary_overlay", "duration": 10, "background": "solid_blue",
                 "lower_third": False, "commentary": True},
            ],
        }
        build_drama_scene_reel(template, 10.0, 1080, 1920, self.tmp,
                               vn_commentary="Bình luận của mình...")
        mock_card.assert_called_once()


class TestComposeDramaVideo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.audio_path = os.path.join(self.tmp, "audio.mp3")
        with open(self.audio_path, "wb") as f:
            f.write(b"fake")

    def test_missing_audio_returns_none(self):
        result = compose_drama_video(
            os.path.join(self.tmp, "nope.mp3"), None, os.path.join(self.tmp, "out.mp4"),
        )
        self.assertIsNone(result)

    @patch("video.tts_client.get_audio_duration")
    def test_zero_duration_returns_none(self, mock_dur):
        mock_dur.return_value = 0
        result = compose_drama_video(self.audio_path, None, os.path.join(self.tmp, "out.mp4"))
        self.assertIsNone(result)

    @patch("video.drama_composer.compose_video")
    @patch("video.drama_composer.build_drama_scene_reel")
    @patch("video.tts_client.get_audio_duration")
    def test_falls_back_to_plain_compose_when_reel_fails(self, mock_dur, mock_reel, mock_compose):
        mock_dur.return_value = 30.0
        mock_reel.return_value = None
        mock_compose.return_value = "/tmp/out.mp4"
        result = compose_drama_video(self.audio_path, None, os.path.join(self.tmp, "out.mp4"))
        self.assertEqual(result, "/tmp/out.mp4")
        _, kwargs = mock_compose.call_args
        self.assertNotIn("bg_video", kwargs)

    @patch("video.drama_composer.compose_video")
    @patch("video.drama_composer.build_drama_scene_reel")
    @patch("video.tts_client.get_audio_duration")
    def test_uses_reel_as_bg_video_when_available(self, mock_dur, mock_reel, mock_compose):
        mock_dur.return_value = 30.0
        mock_reel.return_value = "/tmp/scene_reel.mp4"
        mock_compose.return_value = "/tmp/out.mp4"
        result = compose_drama_video(self.audio_path, None, os.path.join(self.tmp, "out.mp4"))
        self.assertEqual(result, "/tmp/out.mp4")
        _, kwargs = mock_compose.call_args
        self.assertEqual(kwargs.get("bg_video"), "/tmp/scene_reel.mp4")

    @patch("video.drama_composer.compose_video")
    @patch("video.drama_composer.build_drama_scene_reel")
    @patch("video.tts_client.get_audio_duration")
    @patch("video.audio_mixer.mix_background_music")
    @patch("video.audio_mixer.pick_music")
    @patch("video.drama_composer.config")
    def test_bgm_mixed_in_when_enabled_and_music_available(
        self, mock_config, mock_pick, mock_mix, mock_dur, mock_reel, mock_compose,
    ):
        mock_config.ENABLE_BGM = True
        mock_config.DRAMA_MUSIC_DIR = "/fake/music_drama"
        mock_dur.return_value = 30.0
        mock_pick.return_value = "/fake/music_drama/tense_minimal_loop.mp3"
        mock_mix.return_value = "/tmp/mixed.m4a"
        mock_reel.return_value = "/tmp/scene_reel.mp4"
        mock_compose.return_value = "/tmp/out.mp4"

        compose_drama_video(self.audio_path, None, os.path.join(self.tmp, "out.mp4"))

        mock_pick.assert_called_once_with("/fake/music_drama", preferred_name="tense_minimal_loop.mp3")
        mock_mix.assert_called_once()
        args, kwargs = mock_mix.call_args
        self.assertEqual(args[0], self.audio_path)
        self.assertEqual(kwargs.get("music_path"), "/fake/music_drama/tense_minimal_loop.mp3")
        # The mixed audio path should flow into compose_video, not the raw voice track.
        compose_args, _ = mock_compose.call_args
        self.assertEqual(compose_args[0], "/tmp/mixed.m4a")

    @patch("video.drama_composer.compose_video")
    @patch("video.drama_composer.build_drama_scene_reel")
    @patch("video.tts_client.get_audio_duration")
    @patch("video.audio_mixer.mix_background_music")
    @patch("video.audio_mixer.pick_music")
    @patch("video.drama_composer.config")
    def test_bgm_skipped_when_disabled(
        self, mock_config, mock_pick, mock_mix, mock_dur, mock_reel, mock_compose,
    ):
        mock_config.ENABLE_BGM = False
        mock_dur.return_value = 30.0
        mock_reel.return_value = "/tmp/scene_reel.mp4"
        mock_compose.return_value = "/tmp/out.mp4"

        compose_drama_video(self.audio_path, None, os.path.join(self.tmp, "out.mp4"))

        mock_pick.assert_not_called()
        mock_mix.assert_not_called()
        compose_args, _ = mock_compose.call_args
        self.assertEqual(compose_args[0], self.audio_path)

    @patch("video.drama_composer.compose_video")
    @patch("video.drama_composer.build_drama_scene_reel")
    @patch("video.tts_client.get_audio_duration")
    @patch("video.audio_mixer.mix_background_music")
    @patch("video.audio_mixer.pick_music")
    @patch("video.drama_composer.config")
    def test_bgm_skipped_when_no_music_available(
        self, mock_config, mock_pick, mock_mix, mock_dur, mock_reel, mock_compose,
    ):
        mock_config.ENABLE_BGM = True
        mock_config.DRAMA_MUSIC_DIR = "/fake/music_drama"
        mock_dur.return_value = 30.0
        mock_pick.return_value = None
        mock_reel.return_value = "/tmp/scene_reel.mp4"
        mock_compose.return_value = "/tmp/out.mp4"

        compose_drama_video(self.audio_path, None, os.path.join(self.tmp, "out.mp4"))

        mock_mix.assert_not_called()
        compose_args, _ = mock_compose.call_args
        self.assertEqual(compose_args[0], self.audio_path)


@unittest.skipUnless(HAS_FFMPEG and HAS_FFPROBE, "ffmpeg/ffprobe not installed")
class TestBuildDramaSceneReelRealFfmpeg(unittest.TestCase):
    """End-to-end smoke test: real ffmpeg renders + concats a tiny scene reel."""

    def test_real_reel_render(self):
        tmp = tempfile.mkdtemp()
        template = {
            "duration_target": 2,
            "scenes": [
                {"type": "hook", "duration": 1, "background": "gradient_warm",
                 "lower_third": False, "commentary": False},
                {"type": "cta", "duration": 1, "background": "solid_blue",
                 "lower_third": False, "commentary": False},
            ],
        }
        reel = build_drama_scene_reel(template, 2.0, 320, 240, tmp)
        self.assertIsNotNone(reel)
        self.assertTrue(os.path.exists(reel))

        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=width,height",
             "-of", "csv=p=0", reel],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(probe.returncode, 0)
        self.assertIn("320,240", probe.stdout.strip())


if __name__ == "__main__":
    unittest.main()
