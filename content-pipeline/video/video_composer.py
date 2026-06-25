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
                  video_type: str = "long", bg_video: str | None = None,
                  bg_videos: list[str] | None = None) -> str | None:
    """Compose final video from audio + background + subtitles.

    Args:
        audio_path: Path to audio file (.mp3).
        subtitle_path: Path to subtitle file (.srt).
        output_path: Path to save output video (.mp4).
        video_type: "long" (16:9 landscape) or "short" (9:16 vertical).
        bg_video: Path to background video. If None, uses default from config.
        bg_videos: Optional list of clips for multi-clip background (P1). When
            it has >1 entry, they are pre-combined into one background track
            (keeping the final compose at a constant input count). Falls back to
            ``bg_video`` / the first clip on failure.

    Returns:
        Path to the video file, or None on failure.
    """
    # Select dimensions/fontsize based on type
    if video_type == "short":
        fontsize = config.SUBTITLE_FONTSIZE_SHORT
        width, height = 1080, 1920
        default_bg = config.BG_VIDEO_PORTRAIT
    else:
        fontsize = config.SUBTITLE_FONTSIZE_LONG
        width, height = 1920, 1080
        default_bg = config.BG_VIDEO_LANDSCAPE

    # Multi-clip background (P1): pre-combine into one track when ≥2 clips.
    _combined_bg = None
    if bg_videos:
        usable = [c for c in bg_videos if c and os.path.exists(c)]
        if len(usable) >= 2:
            _dur = get_audio_duration(audio_path)
            _combined_bg = _combine_backgrounds(usable, width, height, _dur,
                                                config.BG_CLIP_SECONDS)
        if bg_video is None and usable:
            bg_video = usable[0]
    if _combined_bg:
        bg_video = _combined_bg

    if bg_video is None:
        bg_video = default_bg

    if not os.path.exists(bg_video):
        logger.warning("Background video not found: %s — generating solid-color fallback", bg_video)
        bg_video = _generate_solid_background(bg_video, width, height)
        if bg_video is None:
            logger.error("Failed to generate fallback background")
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

        return _compose_with_subtitles(bg_video, audio_path, output_path,
                                       width, height, audio_duration,
                                       subtitle_entries, sub_pngs, tmpdir)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _generate_solid_background(output_path: str, width: int, height: int,
                                color: str = "0x0d1b2a", duration: int = 10) -> str | None:
    """Generate a short solid-color loopable background video using ffmpeg."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color=c={color}:size={width}x{height}:rate=30:duration={duration}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        output_path,
    ]
    try:
        subprocess.run(cmd, capture_output=True, check=True)
        logger.info("Generated solid background: %s", output_path)
        return output_path
    except subprocess.CalledProcessError as exc:
        logger.error("Failed to generate solid background: %s", exc.stderr.decode()[:200])
        return None


def build_compose_command(bg_video: str, audio_path: str, output_path: str,
                          width: int, height: int, audio_duration: float,
                          subtitle_track: str | None = None) -> list[str]:
    """Build the final ffmpeg compose command (pure — no I/O, unit-testable).

    The number of ffmpeg inputs is CONSTANT regardless of how many subtitle
    lines the video has: 2 (bg + audio) without subtitles, or 3 with a single
    pre-rendered subtitle track. This is the O(1) replacement for the old
    one-input-per-subtitle-line approach.

    Args:
        subtitle_track: Path to a single transparent subtitle video (e.g. the
            qtrle .mov produced by the subtitle-track pass). None → no overlay.
    """
    scale_pad = (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                 f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black")

    cmd = ["ffmpeg", "-y", "-stream_loop", "-1", "-i", bg_video, "-i", audio_path]

    if subtitle_track:
        cmd += ["-i", subtitle_track]  # input 2: single subtitle track
        cmd += [
            "-filter_complex",
            f"[0:v]{scale_pad}[base];[base][2:v]overlay=shortest=0[v]",
            "-map", "[v]", "-map", "1:a",
        ]
    else:
        cmd += ["-vf", scale_pad, "-map", "0:v", "-map", "1:a"]

    cmd += [
        "-t", str(audio_duration),
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        "-shortest",
        output_path,
    ]
    return cmd


def build_multi_bg_command(clips: list[str], output_path: str,
                           width: int, height: int, total_duration: float,
                           clip_seconds: int = 6) -> list[str]:
    """Build the ffmpeg command that combines clips into one background (pure).

    Cuts a *clip_seconds* window from each clip (looping inputs so short clips
    still fill the window), scales/pads to WxH, and concatenates enough cycled
    segments to cover *total_duration*. Number of ffmpeg inputs == number of
    distinct clips (bounded by BG_CLIP_COUNT), independent of total duration.
    """
    if clip_seconds <= 0:
        clip_seconds = 6
    n_clips = len(clips)
    segs_needed = max(1, -(-int(total_duration) // clip_seconds))  # ceil division

    cmd = ["ffmpeg", "-y"]
    for clip in clips:
        cmd += ["-stream_loop", "-1", "-i", clip]

    scale_pad = (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                 f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black")
    parts = []
    labels = []
    for k in range(segs_needed):
        idx = k % n_clips
        parts.append(
            f"[{idx}:v]trim=duration={clip_seconds},setpts=PTS-STARTPTS,"
            f"{scale_pad}[s{k}]"
        )
        labels.append(f"[s{k}]")
    parts.append("".join(labels) + f"concat=n={segs_needed}:v=1:a=0[bgout]")

    cmd += [
        "-filter_complex", ";".join(parts),
        "-map", "[bgout]",
        "-t", str(total_duration),
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-pix_fmt", "yuv420p",
        output_path,
    ]
    return cmd


def _combine_backgrounds(clips: list[str], width: int, height: int,
                         total_duration: float, clip_seconds: int) -> str | None:
    """Run the multi-bg pre-pass; returns combined path or None on failure."""
    if total_duration <= 0:
        return None
    out = os.path.join(tempfile.gettempdir(),
                       f"combined_bg_{os.getpid()}_{len(clips)}.mp4")
    cmd = build_multi_bg_command(clips, out, width, height, total_duration,
                                 clip_seconds)
    logger.info("Combining %d background clips into one track...", len(clips))
    return _run_ffmpeg(cmd, out)


def build_subtitle_concat(subtitle_entries: list[tuple[float, float, str]],
                          png_paths: list[str], blank_png: str,
                          audio_duration: float, concat_path: str) -> str:
    """Write an ffconcat playlist describing the subtitle timeline.

    Each subtitle PNG is shown for its window; gaps (and the head/tail) are
    filled with a fully-transparent blank PNG. This lets a SINGLE ffmpeg
    concat-demuxer pass build one transparent subtitle track — the image paths
    live in the playlist file, not on the command line, so command length is
    O(1) in the number of subtitles.

    Returns the path written. Pure-ish (writes one text file; no ffmpeg).
    """
    lines = ["ffconcat version 1.0"]
    cursor = 0.0
    last_file = blank_png

    for (start, end, _text), png in zip(subtitle_entries, png_paths):
        if start > cursor + 1e-3:
            lines.append(f"file '{blank_png}'")
            lines.append(f"duration {start - cursor:.3f}")
        dur = max(0.0, end - start)
        lines.append(f"file '{png}'")
        lines.append(f"duration {dur:.3f}")
        last_file = png
        cursor = end

    if audio_duration > cursor + 1e-3:
        lines.append(f"file '{blank_png}'")
        lines.append(f"duration {audio_duration - cursor:.3f}")
        last_file = blank_png

    # concat-demuxer quirk: the final entry's duration is only honoured if the
    # last file is listed once more after its duration directive.
    lines.append(f"file '{last_file}'")

    with open(concat_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return concat_path


def _build_subtitle_track_cmd(concat_path: str, out_path: str,
                              fps: int = 30) -> list[str]:
    """Build the ffmpeg command that renders the transparent subtitle track.

    Uses qtrle (lossless, alpha-capable) so the overlay preserves transparency.
    """
    return [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", concat_path,
        "-vf", f"fps={fps},format=rgba",
        "-c:v", "qtrle",
        out_path,
    ]


def _build_blank_png(width: int, height: int, tmpdir: str) -> str | None:
    """Create a fully-transparent WxH PNG used to fill subtitle gaps."""
    try:
        from PIL import Image
    except ImportError:
        logger.error("Pillow not installed — cannot build blank subtitle frame")
        return None
    path = os.path.join(tmpdir, "blank.png")
    Image.new("RGBA", (width, height), (0, 0, 0, 0)).save(path, "PNG")
    return path


def _compose_without_subtitles(bg_video: str, audio_path: str, output_path: str,
                                width: int, height: int, audio_duration: float) -> str | None:
    """Compose video without any subtitle overlay (2 ffmpeg inputs)."""
    cmd = build_compose_command(bg_video, audio_path, output_path,
                                width, height, audio_duration)
    return _run_ffmpeg(cmd, output_path)


def _compose_with_subtitles(bg_video: str, audio_path: str, output_path: str,
                            width: int, height: int, audio_duration: float,
                            subtitle_entries: list[tuple[float, float, str]],
                            sub_pngs: list[str], tmpdir: str) -> str | None:
    """Compose video by overlaying a single pre-built transparent subtitle track.

    Two passes, both O(1) in command-line inputs:
    1. Build one transparent subtitle .mov from the per-line PNGs via the
       concat demuxer (image paths live in a playlist file).
    2. Overlay that single track onto the scaled/padded background + audio.

    Falls back to a subtitle-free compose if the track cannot be built, so the
    pipeline never dies on subtitle issues.
    """
    blank_png = _build_blank_png(width, height, tmpdir)
    if blank_png is None:
        return _compose_without_subtitles(bg_video, audio_path, output_path,
                                          width, height, audio_duration)

    concat_path = os.path.join(tmpdir, "subtitles.concat")
    build_subtitle_concat(subtitle_entries, sub_pngs, blank_png,
                          audio_duration, concat_path)

    track_path = os.path.join(tmpdir, "subtitle_track.mov")
    logger.info("Building subtitle track for %d lines (%.1fs)...",
                len(subtitle_entries), audio_duration)
    if _run_ffmpeg(_build_subtitle_track_cmd(concat_path, track_path),
                   track_path) is None:
        logger.warning("Subtitle track build failed — composing without subtitles")
        return _compose_without_subtitles(bg_video, audio_path, output_path,
                                          width, height, audio_duration)

    cmd = build_compose_command(bg_video, audio_path, output_path,
                                width, height, audio_duration,
                                subtitle_track=track_path)
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


def _wrap_text(text: str, font, max_width: int) -> list[str]:
    """Wrap text into multiple lines so each line fits within *max_width* pixels.

    Uses word-by-word measurement via font bounding boxes.
    """
    from PIL import ImageDraw, Image

    # Temporary draw context for measuring only
    tmp = Image.new("RGBA", (1, 1))
    draw = ImageDraw.Draw(tmp)

    words = text.split()
    if not words:
        return [text]

    lines: list[str] = []
    current_line = words[0]

    for word in words[1:]:
        test_line = f"{current_line} {word}"
        bbox = draw.textbbox((0, 0), test_line, font=font)
        line_w = bbox[2] - bbox[0]
        if line_w <= max_width:
            current_line = test_line
        else:
            lines.append(current_line)
            current_line = word

    lines.append(current_line)
    return lines


def _render_one_subtitle(text: str, width: int, height: int,
                         font, output_path: str) -> None:
    """Render a single subtitle entry as a transparent RGBA PNG with word wrapping."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    max_text_width = int(width * 0.80)
    lines = _wrap_text(text, font, max_text_width)

    # Measure line height from a sample line
    sample_bbox = draw.textbbox((0, 0), "Ag", font=font)
    line_h = sample_bbox[3] - sample_bbox[1]
    line_spacing = int(line_h * 0.35)

    total_text_h = len(lines) * line_h + (len(lines) - 1) * line_spacing
    # Position block so its bottom sits at ~85% of frame height
    block_top = int(height * 0.85) - total_text_h

    outline_px = max(2, int(font.size * 0.06))

    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        line_w = bbox[2] - bbox[0]
        x = (width - line_w) // 2
        y = block_top + i * (line_h + line_spacing)

        # Black outline for readability
        for dx in range(-outline_px, outline_px + 1):
            for dy in range(-outline_px, outline_px + 1):
                if dx == 0 and dy == 0:
                    continue
                draw.text((x + dx, y + dy), line, font=font, fill=(0, 0, 0, 230))

        draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))

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
