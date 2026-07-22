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
    # phase-4-detailed.md states duration_target=75 but its own per-scene
    # durations below (copied verbatim: 3+12+30+25+8+12) sum to 90 — another
    # doc inconsistency (see docs/current/prompts-decisions.md for the
    # similar word-count/duration mismatch found in Phase 3). Corrected here
    # to match the actual scene sum rather than silently drifting from it;
    # kept the specific named-scene durations since those came with an
    # explicit rationale ("Hook 3s", "Twist 25s", ...).
    "duration_target": 90,  # seconds
    "scenes": [
        {"type": "hook", "duration": 3, "background": "illustration",
         "fallback": "gradient_warm", "lower_third": False, "commentary": False},
        {"type": "setup", "duration": 12, "background": "illustration",
         "fallback": "gradient_warm", "lower_third": False, "commentary": False},
        {"type": "escalation", "duration": 30, "background": "illustration",
         "fallback": "gradient_cool", "lower_third": True, "commentary": False},
        {"type": "twist", "duration": 25, "background": "illustration_dark",
         "fallback": "solid_blue", "lower_third": False, "commentary": False},
        {"type": "vn_commentary_overlay", "duration": 8, "background": "illustration_dark",
         "fallback": "solid_blue", "lower_third": False, "commentary": True},
        {"type": "reflection_cta", "duration": 12, "background": "illustration",
         "fallback": "gradient_cool", "lower_third": False, "commentary": False},
    ],
    "transitions": "match_cut",
    "music_track": "tense_minimal_loop.mp3",
}
