from __future__ import annotations

"""
MoviePy Composer Engine (P2, optional) — alternative to the FFmpeg composer.

Same signature as video_composer.compose_video so the two are interchangeable
via COMPOSER_ENGINE. MoviePy gives a layer/timeline model that is nicer for
animated subtitles/transitions; it is heavier, so FFmpeg stays the default.

MoviePy is an optional dependency (lazy import). If it is missing or anything
fails, compose() returns None and the caller can fall back.
"""

import logging
import os

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
from video.video_composer import _parse_srt

logger = logging.getLogger(__name__)


def build_subtitle_specs(subtitle_entries: list[tuple[float, float, str]],
                         width: int, height: int,
                         fontsize: int) -> list[dict]:
    """Build MoviePy TextClip specs from SRT entries (pure, unit-testable).

    Returns one dict per subtitle line with text/start/duration/fontsize and a
    bottom-centred position (~78% of frame height), mirroring the FFmpeg layout.
    """
    specs = []
    y = int(height * 0.78)
    for start, end, text in subtitle_entries:
        duration = max(0.0, end - start)
        specs.append({
            "text": text,
            "start": start,
            "duration": duration,
            "fontsize": fontsize,
            "position": ("center", y),
            "size": (int(width * 0.8), None),
        })
    return specs


def _dimensions(video_type: str) -> tuple[int, int, int]:
    if video_type == "short":
        return 1080, 1920, config.SUBTITLE_FONTSIZE_SHORT
    return 1920, 1080, config.SUBTITLE_FONTSIZE_LONG


def compose(audio_path: str, subtitle_path: str, output_path: str,
            video_type: str = "long", bg_video: str | None = None,
            bg_videos: list[str] | None = None) -> str | None:
    """Compose a video with MoviePy. Returns output path or None on failure."""
    try:
        from moviepy import (  # MoviePy 2.x
            AudioFileClip, VideoFileClip, TextClip, CompositeVideoClip,
            concatenate_videoclips,
        )
    except ImportError:
        logger.warning(
            "moviepy not installed — COMPOSER_ENGINE=moviepy unavailable. "
            "Install with: pip install moviepy"
        )
        return None

    if not os.path.exists(audio_path):
        logger.error("Audio not found: %s", audio_path)
        return None

    width, height, fontsize = _dimensions(video_type)

    # Choose background source(s).
    clips_in = [c for c in (bg_videos or []) if c and os.path.exists(c)]
    if not clips_in and bg_video and os.path.exists(bg_video):
        clips_in = [bg_video]
    if not clips_in:
        logger.error("No usable background for moviepy compose")
        return None

    try:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        audio = AudioFileClip(audio_path)
        duration = audio.duration

        bg_clips = [VideoFileClip(c).resized((width, height)) for c in clips_in]
        bg = (bg_clips[0] if len(bg_clips) == 1
              else concatenate_videoclips(bg_clips, method="compose"))
        bg = bg.with_effects(_loop_to(duration)).with_duration(duration)

        layers = [bg]
        font = config.SUBTITLE_FONT if os.path.exists(config.SUBTITLE_FONT) else None
        for spec in build_subtitle_specs(_parse_srt(subtitle_path),
                                         width, height, fontsize):
            txt = (TextClip(text=spec["text"], font=font,
                            font_size=spec["fontsize"], color="white",
                            stroke_color="black", stroke_width=2,
                            size=spec["size"], method="caption")
                   .with_start(spec["start"])
                   .with_duration(spec["duration"])
                   .with_position(spec["position"]))
            layers.append(txt)

        final = CompositeVideoClip(layers, size=(width, height)).with_audio(audio)
        final.write_videofile(output_path, codec="libx264", audio_codec="aac",
                              fps=30, preset="medium")
        logger.info("MoviePy composed: %s", output_path)
        return output_path
    except Exception as e:
        logger.error("MoviePy compose failed: %s", e)
        return None


def _loop_to(duration: float):
    """Return a MoviePy loop effect covering *duration* (2.x vfx.Loop)."""
    from moviepy import vfx
    return [vfx.Loop(duration=duration)]
