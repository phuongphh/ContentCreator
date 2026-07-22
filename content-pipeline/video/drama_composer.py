from __future__ import annotations

"""
Drama Scene Composer (Phase 4 EPIC #4.2 — Video Composer Multi-track).

Builds a Drama Shorts video: pre-renders each template scene as its own short
segment (background + optional lower-third/commentary overlay baked in),
concatenates them into one "scene reel", then feeds that reel as the
background into the EXISTING `video_composer.compose_video()` — reusing its
already-robust audio/subtitle/crop pipeline rather than duplicating it.

Note on `lower_third`/`vn_commentary` inputs: processors/drama_rewriter.py's
output schema (Phase 3) has no structured per-character name/role field —
only free-text `script`. Rather than guess at parsing character names out of
the script, this module takes lower-third data as an explicit optional
argument; callers that have it (e.g. a future manual-annotation step, or a
Phase 3 prompt/schema extension) pass it in, and a scene simply skips its
overlay when it's not supplied — never a hard failure.
"""

import logging
import math
import os
import tempfile

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
from video.video_composer import _scale_filter, _run_ffmpeg, compose_video
from video.templates import load_template
from video.lower_third import render_lower_third
from video.commentary_card import render_commentary_card
from video.image_generator import generate_illustration, cached_illustration

logger = logging.getLogger(__name__)

# Plain ffmpeg lavfi sources — no external dependency, always available.
GRADIENT_SPECS = {
    "gradient_warm": ("0xFF6B35", "0xF7C59F"),
    "gradient_cool": ("0x2C3E50", "0x4A6FA5"),
}
SOLID_SPECS = {
    "solid_blue": "0x1B3A5C",
    "solid_brand": "0x0D1B2A",
}
_FALLBACK_BACKGROUND = "solid_blue"

# All scene segments are normalized to one frame rate so the stream-copy
# concat never mixes fps (lavfi sources default to 25, zoompan is told its
# own fps — without this the reel's timestamps would drift between scenes).
_SCENE_FPS = 30
# Ken Burns zoom travel over a scene (1.0 → 1.0+range). Subtle on purpose:
# strong zooms on AI stills read as cheap slideshow.
_ZOOM_RANGE = 0.10
# eq filter params for "illustration_dark" scenes: dim + slightly desaturate
# so the twist scene reads darker and the commentary card stays legible on
# top of a busy image.
_DARKEN_FILTER = "eq=brightness=-0.18:saturation=0.85"


def _lavfi_source(background: str, width: int, height: int) -> str | None:
    """ffmpeg lavfi source spec (no duration) for a symbolic background name.

    Returns None if `background` isn't a known lavfi spec — caller should
    then treat it as something else (a real file / AI illustration prompt).
    """
    if background in GRADIENT_SPECS:
        c0, c1 = GRADIENT_SPECS[background]
        return f"gradients=s={width}x{height}:c0={c0}:c1={c1}"
    if background in SOLID_SPECS:
        color = SOLID_SPECS[background]
        return f"color=c={color}:s={width}x{height}"
    return None


def build_scene_segment_command(background: str, is_lavfi: bool, duration: float,
                                width: int, height: int, output_path: str,
                                overlay_png: str | None = None,
                                fill: bool = True, motion: bool = False,
                                zoom_in: bool = True,
                                darken: bool = False) -> list[str]:
    """Build the ffmpeg command for ONE scene segment (pure, unit-testable).

    Args:
        background: an lavfi source spec string (is_lavfi=True) or a real
            file path to loop (is_lavfi=False, e.g. an AI illustration PNG).
        overlay_png: lower-third or commentary-card PNG, composited for the
            full segment duration when given.
        motion: still-image sources only — animate a slow Ken Burns zoom via
            zoompan instead of holding a frozen frame (issue #103: static
            AI-image scenes read as dead air). Ignored for lavfi sources.
        zoom_in: zoom direction for `motion` (alternate per scene for variety).
        darken: dim + desaturate the background (illustration_dark scenes),
            applied AFTER scaling so the overlay PNG keeps full brightness.
    """
    scale_pad = _scale_filter(width, height, fill)

    if is_lavfi:
        cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"{background}:d={duration}"]
        chain = f"{scale_pad},fps={_SCENE_FPS}"
    elif motion:
        # A single still frame in; zoompan synthesizes every output frame, so
        # no -stream_loop is needed. Pre-scaling to 2x the target size keeps
        # the subpixel pan smooth (zoompan at 1:1 visibly jitters).
        frames = max(1, math.ceil(duration * _SCENE_FPS))
        big_w, big_h = width * 2, height * 2
        if zoom_in:
            zexpr = f"1+{_ZOOM_RANGE}*on/{frames}"
        else:
            zexpr = f"1+{_ZOOM_RANGE}-{_ZOOM_RANGE}*on/{frames}"
        cmd = ["ffmpeg", "-y", "-i", background]
        chain = (
            f"scale={big_w}:{big_h}:force_original_aspect_ratio=increase,"
            f"crop={big_w}:{big_h},"
            f"zoompan=z='{zexpr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d={frames}:s={width}x{height}:fps={_SCENE_FPS}"
        )
    else:
        cmd = ["ffmpeg", "-y", "-stream_loop", "-1", "-i", background]
        chain = f"{scale_pad},fps={_SCENE_FPS}"

    if darken:
        chain += f",{_DARKEN_FILTER}"

    if overlay_png:
        cmd += ["-i", overlay_png]
        cmd += [
            "-filter_complex",
            f"[0:v]{chain}[base];[base][1:v]overlay=shortest=0[v]",
            "-map", "[v]",
        ]
    else:
        cmd += ["-vf", chain, "-map", "0:v"]

    cmd += [
        "-t", str(duration),
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-pix_fmt", "yuv420p",
        output_path,
    ]
    return cmd


def build_scene_concat_command(concat_path: str, output_path: str) -> list[str]:
    """Build the ffmpeg command that concatenates pre-rendered scene segments.

    All segments share the same codec/size/pix_fmt (encoded that way by
    build_scene_segment_command), so a stream copy concat is safe and fast.
    """
    return [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", concat_path,
        "-c", "copy",
        output_path,
    ]


def _write_scene_concat_playlist(segment_paths: list[str], concat_path: str) -> str:
    lines = [f"file '{p}'" for p in segment_paths]
    with open(concat_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return concat_path


def scaled_scene_durations(template: dict, total_duration: float) -> list[float]:
    """Scale each scene's template duration proportionally to sum to `total_duration`.

    Real narration length varies per story; the template's per-scene
    durations are a relative-weight guideline, not a hard cut.
    """
    scenes = template["scenes"]
    target_total = template.get("duration_target") or sum(s["duration"] for s in scenes)
    if target_total <= 0:
        target_total = len(scenes)
    scale = total_duration / target_total
    return [max(0.1, s["duration"] * scale) for s in scenes]


def _resolve_scene_background(scene: dict, width: int, height: int, index: int,
                              thumbnail_prompt: str | None,
                              gen_state: dict | None = None) -> tuple[str, bool]:
    """Resolve a scene's symbolic `background` key to an ffmpeg input source.

    Returns (source, is_lavfi). Resolution order for illustration scenes
    (issue #103 — a Replicate failure used to drop a scene straight to a
    solid color even when the same story had illustrations sitting in cache):

    1. generate_illustration() for variant (index % DRAMA_ILLUSTRATION_VARIANTS)
       — a cache hit is free; a live API call otherwise. After the FIRST live
       failure in this reel run (`gen_state`), later scenes skip the API
       entirely: each failed attempt can burn up to the Replicate poll
       timeout, and one hard failure (revoked token, rate limit) predicts the
       next five.
    2. cached_illustration() — reuse ANY cached variant of this story's
       prompt (e.g. the thumbnail's index 0) at zero cost.
    3. The scene's declarative `fallback` lavfi key (its designed color
       mood), then _FALLBACK_BACKGROUND as the absolute last resort.
    """
    background_key = scene["background"]
    state = gen_state if gen_state is not None else {}

    lavfi = _lavfi_source(background_key, width, height)
    if lavfi is not None:
        return lavfi, True

    if background_key in ("illustration", "illustration_dark") and thumbnail_prompt:
        variant = index % config.DRAMA_ILLUSTRATION_VARIANTS
        if not state.get("generation_failed"):
            illustration = generate_illustration(thumbnail_prompt, index=variant)
            if illustration is not None:
                return illustration, False
            state["generation_failed"] = True
            logger.warning(
                "Illustration generation failed (scene %d) — remaining scenes "
                "will reuse cached illustrations or color fallbacks this run",
                index,
            )
        cached = cached_illustration(thumbnail_prompt, preferred_index=variant)
        if cached is not None:
            logger.info("Scene %d: reusing cached illustration %s",
                        index, os.path.basename(cached))
            return cached, False
        logger.info("No AI illustration available for scene %d — using fallback background", index)

    # Unresolvable symbolic background (e.g. "screen_record" outside the AI
    # track, or illustration unavailable) — the scene's declared fallback
    # color first, then the safe always-available default.
    fallback = _lavfi_source(scene.get("fallback", ""), width, height)
    if fallback is not None:
        return fallback, True
    return _lavfi_source(_FALLBACK_BACKGROUND, width, height), True


def build_drama_scene_reel(
    template: dict,
    total_duration: float,
    width: int,
    height: int,
    tmpdir: str,
    thumbnail_prompt: str | None = None,
    lower_third: dict | None = None,
    vn_commentary: str | None = None,
    fill: bool = True,
) -> str | None:
    """Render every scene as its own segment and concat them into one reel.

    Returns the reel's path, or None on failure (caller should fall back to
    a plain single-background compose rather than produce nothing).
    """
    scenes = template["scenes"]
    durations = scaled_scene_durations(template, total_duration)

    # Shared across this run's scenes: after one live Replicate failure the
    # remaining scenes go straight to cache-reuse instead of re-hitting the
    # API (see _resolve_scene_background).
    gen_state: dict = {}

    segment_paths = []
    for i, (scene, duration) in enumerate(zip(scenes, durations)):
        overlay_png = None
        if scene.get("lower_third") and lower_third and lower_third.get("name"):
            overlay_png = render_lower_third(
                lower_third.get("name", ""), lower_third.get("role", ""),
                width, height, os.path.join(tmpdir, f"overlay_{i}.png"),
            )
        elif scene.get("commentary") and vn_commentary:
            overlay_png = render_commentary_card(
                vn_commentary, width, height, os.path.join(tmpdir, f"overlay_{i}.png"),
            )

        source, is_lavfi = _resolve_scene_background(scene, width, height, i,
                                                     thumbnail_prompt, gen_state)
        darken = (not is_lavfi) and scene["background"] == "illustration_dark"
        motion = (not is_lavfi) and config.DRAMA_SCENE_MOTION

        seg_path = os.path.join(tmpdir, f"scene_{i}.mp4")
        cmd = build_scene_segment_command(source, is_lavfi, duration, width, height,
                                          seg_path, overlay_png=overlay_png, fill=fill,
                                          motion=motion, zoom_in=(i % 2 == 0),
                                          darken=darken)
        rendered = _run_ffmpeg(cmd, seg_path)
        if rendered is None and motion:
            # Ken Burns is a nice-to-have — a zoompan hiccup (odd ffmpeg build,
            # unusual image) must not cost the whole video. Retry static.
            logger.warning("Scene %d motion render failed — retrying static", i)
            cmd = build_scene_segment_command(source, is_lavfi, duration, width,
                                              height, seg_path,
                                              overlay_png=overlay_png, fill=fill,
                                              darken=darken)
            rendered = _run_ffmpeg(cmd, seg_path)
        if rendered is None:
            logger.error("Failed to render scene %d (%s) — aborting scene reel",
                         i, scene["type"])
            return None
        segment_paths.append(seg_path)

    concat_path = os.path.join(tmpdir, "scenes.concat")
    _write_scene_concat_playlist(segment_paths, concat_path)
    reel_path = os.path.join(tmpdir, "scene_reel.mp4")
    if _run_ffmpeg(build_scene_concat_command(concat_path, reel_path), reel_path) is None:
        logger.error("Failed to concatenate scene reel")
        return None
    return reel_path


def compose_drama_video(
    audio_path: str,
    subtitle_path: str | None,
    output_path: str,
    thumbnail_prompt: str | None = None,
    lower_third: dict | None = None,
    vn_commentary: str | None = None,
    video_format: str = "shorts",
) -> str | None:
    """Compose a full Drama video: multi-scene reel + audio + subtitles.

    Falls back to the plain single-background compose (video_composer's
    existing behaviour, no scene reel) if the reel fails to render, so a
    Drama render never produces nothing at all.
    """
    from video.tts_client import get_audio_duration

    if not os.path.exists(audio_path):
        logger.error("Audio file not found: %s", audio_path)
        return None
    audio_duration = get_audio_duration(audio_path)
    if audio_duration <= 0:
        logger.error("Could not determine audio duration")
        return None

    template = load_template("drama", video_format)
    width, height = (1080, 1920) if template["format"] == "9:16" else (1920, 1080)

    with tempfile.TemporaryDirectory() as tmpdir:
        if config.ENABLE_BGM:
            from video.audio_mixer import mix_background_music, pick_music
            music_path = pick_music(config.DRAMA_MUSIC_DIR, preferred_name=template.get("music_track"))
            if music_path:
                # .m4a: the mixer encodes AAC, which an .mp3 muxer rejects.
                mixed_path = os.path.join(tmpdir, "audio_bgm.m4a")
                audio_path = mix_background_music(audio_path, mixed_path, music_path=music_path)

        reel = build_drama_scene_reel(
            template, audio_duration, width, height, tmpdir,
            thumbnail_prompt=thumbnail_prompt, lower_third=lower_third,
            vn_commentary=vn_commentary, fill=True,
        )
        if reel is None:
            logger.warning("Scene reel failed — falling back to plain background compose")
            return compose_video(audio_path, subtitle_path, output_path, video_type="short")

        return compose_video(audio_path, subtitle_path, output_path,
                             video_type="short", bg_video=reel)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Drama composer ready — call compose_drama_video() with real audio/story data.")
