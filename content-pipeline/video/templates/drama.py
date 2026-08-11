from __future__ import annotations

"""
Drama Shorts template (Phase 4 EPIC #4.2/#4.3).

Scene list + target timing for a 75s TikTok/Shorts-format Drama video.
Per-scene ``duration`` is a guideline for the composer (video/drama_composer.py
scales scenes to fit the actual narration length) — not a hard cut.

``background`` is a symbolic key the composer resolves:
- "illustration" / "illustration_dark" — AI-generated (video/image_generator.py),
  falling back to a gradient/solid color if generation is unavailable.
- "gradient_warm" / "gradient_cool" / "solid_blue" — plain ffmpeg lavfi sources,
  no external dependency.

Issue #103: originally only 3 of 6 scenes used illustrations (the rest were
gradients/solids BY DESIGN), and any Replicate failure dropped an illustration
scene to a solid color — a whole video could end up one AI image + five flat
color slabs. Now every scene is illustration-first; the old gradient/solid key
moved to the per-scene ``fallback`` field (used only when no illustration can
be generated OR reused from cache), so the designed color mood is the LAST
resort, not the default look. Scene i uses illustration variant
(i % config.DRAMA_ILLUSTRATION_VARIANTS) — variety without one API call per
scene.
"""

DRAMA_SHORTS_TEMPLATE = {
    "format": "9:16",
    # Keep the target equal to the remaining visual scene weights. The old
    # 8-second full-text commentary scene was removed: narration and subtitles
    # still carry that content without covering the video with a text page.
    "duration_target": 82,  # seconds
    "scenes": [
        {"type": "hook", "duration": 3, "background": "illustration",
         "fallback": "gradient_warm", "lower_third": False},
        {"type": "setup", "duration": 12, "background": "illustration",
         "fallback": "gradient_warm", "lower_third": False},
        {"type": "escalation", "duration": 30, "background": "illustration",
         "fallback": "gradient_cool", "lower_third": True},
        {"type": "twist", "duration": 25, "background": "illustration_dark",
         "fallback": "solid_blue", "lower_third": False},
        {"type": "reflection_cta", "duration": 12, "background": "illustration",
         "fallback": "gradient_cool", "lower_third": False},
    ],
    "transitions": "match_cut",
    "music_track": "tense_minimal_loop.mp3",
}
