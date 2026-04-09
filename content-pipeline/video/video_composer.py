from __future__ import annotations

"""
Video Composer — Ghép audio + background video + subtitle thành video hoàn chỉnh.

Dùng FFmpeg subprocess:
- Loop background video nếu ngắn hơn audio
- Burn subtitle vào video (hardcoded)
- Output: MP4 H.264 + AAC
"""

import logging
import os
import subprocess

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
from video.tts_client import get_audio_duration

logger = logging.getLogger(__name__)


def compose_video(audio_path: str, subtitle_path: str, output_path: str,
                  video_type: str = "long") -> str | None:
    """Compose final video from audio + background + subtitles.

    Args:
        audio_path: Path to audio file (.mp3).
        subtitle_path: Path to subtitle file (.srt).
        output_path: Path to save output video (.mp4).
        video_type: "long" (16:9 landscape) or "short" (9:16 vertical).

    Returns:
        Path to the video file, or None on failure.
    """
    # Select background and settings based on type
    if video_type == "short":
        bg_video = config.BG_VIDEO_PORTRAIT
        fontsize = config.SUBTITLE_FONTSIZE_SHORT
        resolution = "1080:1920"
    else:
        bg_video = config.BG_VIDEO_LANDSCAPE
        fontsize = config.SUBTITLE_FONTSIZE_LONG
        resolution = "1920:1080"

    if not os.path.exists(bg_video):
        logger.error("Background video not found: %s", bg_video)
        return None
    if not os.path.exists(audio_path):
        logger.error("Audio file not found: %s", audio_path)
        return None

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    audio_duration = get_audio_duration(audio_path)
    if audio_duration <= 0:
        logger.error("Could not determine audio duration")
        return None

    # Build subtitle filter.
    # Wrap path in single quotes so FFmpeg's filter parser handles spaces and
    # other special characters correctly (single-quoted values are literal in
    # FFmpeg's avfilter option parser).
    escaped_sub = _escape_ffmpeg_path(subtitle_path)
    font_style = ""
    if config.SUBTITLE_FONT and os.path.exists(config.SUBTITLE_FONT):
        escaped_font = config.SUBTITLE_FONT.replace("\\", "/").replace(":", "\\:")
        font_style = f":force_style='FontName=NotoSans,Fontfile={escaped_font},FontSize={fontsize}'"

    sub_filter = f"subtitles='{escaped_sub}'{font_style}"

    # FFmpeg command: loop bg video, overlay audio, burn subtitles
    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1",        # Loop background video infinitely
        "-i", bg_video,              # Input 0: background video
        "-i", audio_path,            # Input 1: audio
        "-t", str(audio_duration),   # Duration = audio length
        "-vf", f"scale={resolution}:force_original_aspect_ratio=decrease,"
               f"pad={resolution}:(ow-iw)/2:(oh-ih)/2:black,"
               f"{sub_filter}",
        "-map", "0:v",               # Video from bg
        "-map", "1:a",               # Audio from TTS
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",   # Web-optimized
        "-shortest",
        output_path,
    ]

    logger.info("Composing %s video (%.1fs)...", video_type, audio_duration)
    logger.debug("FFmpeg cmd: %s", " ".join(cmd))

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600,
        )
        if result.returncode != 0:
            logger.error("FFmpeg failed:\n%s", result.stderr[-2000:])
            return None

        logger.info("Video composed: %s", output_path)
        return output_path

    except subprocess.TimeoutExpired:
        logger.error("FFmpeg timed out after 600s")
        return None
    except FileNotFoundError:
        logger.error("ffmpeg not found — install ffmpeg first")
        return None


def _escape_ffmpeg_path(path: str) -> str:
    """Escape special characters in path for FFmpeg filter syntax."""
    # Simple escaping for now
    path = path.replace("\\", "/")
    path = path.replace(":", "\\:")
    path = path.replace("'", "\\\\'")
    return path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # Verify ffmpeg is installed
    try:
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        version = result.stdout.decode().split("\n")[0]
        print(f"ffmpeg: {version}")
    except FileNotFoundError:
        print("ffmpeg: NOT FOUND — install ffmpeg first")
