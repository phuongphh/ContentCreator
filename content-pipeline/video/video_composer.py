from __future__ import annotations

"""
Video Composer — Ghép audio + background video + subtitle thành video hoàn chỉnh.

Dùng FFmpeg subprocess:
- Loop background video nếu ngắn hơn audio
- Burn subtitle vào video (hardcoded) qua Pillow overlay
- Output: MP4 H.264 + AAC

Approach: vì ffmpeg thông thường (Homebrew) không compile với --enable-libass,
filter `subtitles` không khả dụng. Thay vào đó dùng Pillow để render từng
subtitle entry thành PNG rồi overlay qua ffmpeg `overlay` filter.
"""

import logging
import os
import re
import subprocess
import tempfile

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
from video.tts_client import get_audio_duration

logger = logging.getLogger(__name__)

# Fallback system fonts (tried in order if config.SUBTITLE_FONT not found)
_FALLBACK_FONTS = [
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Geneva.ttf",
]


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
        width, height = 1080, 1920
    else:
        bg_video = config.BG_VIDEO_LANDSCAPE
        fontsize = config.SUBTITLE_FONTSIZE_LONG
        width, height = 1920, 1080

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

    with tempfile.TemporaryDirectory() as tmpdir:
        subtitle_entries = _parse_srt(subtitle_path)
        if not subtitle_entries:
            logger.warning("No subtitle entries found — composing without subtitles")
            return _compose_without_subtitles(bg_video, audio_path, output_path,
                                              width, height, audio_duration)

        sub_pngs = _render_subtitle_pngs(subtitle_entries, width, height, fontsize, tmpdir)
        if not sub_pngs:
            logger.warning("Failed to render subtitle PNGs — composing without subtitles")
            return _compose_without_subtitles(bg_video, audio_path, output_path,
                                              width, height, audio_duration)

        return _compose_with_overlay(bg_video, audio_path, output_path,
                                     width, height, audio_duration,
                                     subtitle_entries, sub_pngs)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _compose_without_subtitles(bg_video: str, audio_path: str, output_path: str,
                                width: int, height: int, audio_duration: float) -> str | None:
    """Compose video without subtitle overlay."""
    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1",
        "-i", bg_video,
        "-i", audio_path,
        "-t", str(audio_duration),
        "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
               f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black",
        "-map", "0:v",
        "-map", "1:a",
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        "-shortest",
        output_path,
    ]
    return _run_ffmpeg(cmd, output_path)


def _compose_with_overlay(bg_video: str, audio_path: str, output_path: str,
                          width: int, height: int, audio_duration: float,
                          subtitle_entries: list[tuple[float, float, str]],
                          sub_pngs: list[str]) -> str | None:
    """Compose video with Pillow-rendered subtitle PNGs overlaid via ffmpeg overlay filter."""
    # Build ffmpeg inputs: bg video, audio, then one PNG per subtitle entry
    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1", "-i", bg_video,   # input 0: background
        "-i", audio_path,                         # input 1: audio
    ]
    for png_path in sub_pngs:
        cmd += ["-loop", "1", "-i", png_path]     # inputs 2+N: subtitle PNGs

    # Build filter_complex chain
    # Step 1: scale + pad background
    filter_parts = [
        f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black[scaled]"
    ]

    # Step 2: chain overlay for each subtitle entry
    current_label = "scaled"
    for i, (start, end, _text) in enumerate(subtitle_entries):
        input_idx = i + 2  # inputs 0 and 1 are bg+audio
        next_label = f"v{i+1}" if i < len(subtitle_entries) - 1 else "vout"
        filter_parts.append(
            f"[{current_label}][{input_idx}:v]overlay=enable='between(t,{start:.3f},{end:.3f})'[{next_label}]"
        )
        current_label = next_label

    filter_complex = ";".join(filter_parts)

    cmd += [
        "-filter_complex", filter_complex,
        "-map", f"[{current_label}]",
        "-map", "1:a",
        "-t", str(audio_duration),
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        "-shortest",
        output_path,
    ]

    logger.info("Composing video with %d subtitle overlays (%.1fs)...",
                len(subtitle_entries), audio_duration)
    logger.debug("filter_complex:\n%s", filter_complex)
    return _run_ffmpeg(cmd, output_path)


def _render_subtitle_pngs(
    subtitle_entries: list[tuple[float, float, str]],
    width: int, height: int, fontsize: int,
    tmpdir: str,
) -> list[str] | None:
    """Render each subtitle entry as a transparent RGBA PNG using Pillow.

    Returns list of PNG paths (same order as subtitle_entries), or None on error.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        logger.error("Pillow not installed — run: pip install Pillow")
        return None

    font = _load_font(fontsize)
    if font is None:
        logger.error("No usable font found for subtitle rendering")
        return None

    png_paths = []
    for i, (_start, _end, text) in enumerate(subtitle_entries):
        png_path = os.path.join(tmpdir, f"sub_{i:04d}.png")
        try:
            _render_one_subtitle(text, width, height, font, png_path)
            png_paths.append(png_path)
        except Exception as exc:
            logger.error("Failed to render subtitle %d ('%s'): %s", i, text[:40], exc)
            return None

    return png_paths


def _render_one_subtitle(text: str, width: int, height: int,
                         font, output_path: str) -> None:
    """Render a single subtitle line as a transparent RGBA PNG."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Measure text
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    # Clamp width: if text wider than 90% of frame, we just let it clip
    x = max(0, (width - text_w) // 2)
    y = int(height * 0.82)  # 82% down from top

    # Black outline for readability
    outline_px = max(2, int(font.size * 0.06))
    for dx in range(-outline_px, outline_px + 1):
        for dy in range(-outline_px, outline_px + 1):
            if dx == 0 and dy == 0:
                continue
            draw.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0, 230))

    draw.text((x, y), text, font=font, fill=(255, 255, 255, 255))
    img.save(output_path, "PNG")


def _load_font(fontsize: int):
    """Load font: try config path first, then fallbacks."""
    try:
        from PIL import ImageFont
    except ImportError:
        return None

    candidates = []
    if config.SUBTITLE_FONT and os.path.exists(config.SUBTITLE_FONT):
        candidates.append(config.SUBTITLE_FONT)
    candidates.extend(_FALLBACK_FONTS)

    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, fontsize)
            except Exception:
                continue

    # Last resort: Pillow default bitmap font (no Vietnamese, but won't crash)
    logger.warning("No TTF font found — using Pillow default (Vietnamese may not render correctly)")
    return ImageFont.load_default()


def _parse_srt(srt_path: str) -> list[tuple[float, float, str]]:
    """Parse SRT file into list of (start_sec, end_sec, text) tuples."""
    if not os.path.exists(srt_path):
        logger.error("SRT file not found: %s", srt_path)
        return []

    try:
        with open(srt_path, encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        logger.error("Failed to read SRT: %s", e)
        return []

    entries = []
    # Each SRT block: index, timecode line, text, blank line
    blocks = re.split(r"\n\s*\n", content.strip())
    for block in blocks:
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        # lines[0] is index, lines[1] is timecode, lines[2+] is text
        tc_match = re.match(
            r"(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})",
            lines[1],
        )
        if not tc_match:
            continue
        start = _srt_time_to_sec(tc_match.group(1))
        end = _srt_time_to_sec(tc_match.group(2))
        text = " ".join(lines[2:]).strip()
        if text:
            entries.append((start, end, text))

    logger.debug("Parsed %d subtitle entries from %s", len(entries), srt_path)
    return entries


def _srt_time_to_sec(tc: str) -> float:
    """Convert SRT timecode HH:MM:SS,mmm to float seconds."""
    tc = tc.replace(",", ".")
    parts = tc.split(":")
    hours = int(parts[0])
    minutes = int(parts[1])
    secs = float(parts[2])
    return hours * 3600 + minutes * 60 + secs


def _run_ffmpeg(cmd: list[str], output_path: str) -> str | None:
    """Run an ffmpeg command, log errors, return output_path on success."""
    logger.debug("FFmpeg cmd: %s", " ".join(cmd))
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        version = result.stdout.decode().split("\n")[0]
        print(f"ffmpeg: {version}")
    except FileNotFoundError:
        print("ffmpeg: NOT FOUND — install ffmpeg first")
