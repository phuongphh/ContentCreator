from __future__ import annotations

"""
AI Shorts template (Phase 4 EPIC #4.2 — kept for template-system symmetry).

The AI track's actual rendering still goes through the existing single-
background composer (video/video_composer.py) — this template documents its
scene shape for `load_template()` rather than changing how it renders today.
"screen_record" is a manually-provided screen-recording clip (Phuong quay tay
phần demo — see phase-4-detailed.md §3.4); the pipeline only ghép (composes),
it never generates that footage.
"""

AI_SHORTS_TEMPLATE = {
    "format": "9:16",
    "duration_target": 45,  # seconds
    "scenes": [
        {"type": "hook", "duration": 3, "background": "screen_record"},
        {"type": "tip_demo", "duration": 35, "background": "screen_record"},
        {"type": "cta", "duration": 7, "background": "solid_brand"},
    ],
}
