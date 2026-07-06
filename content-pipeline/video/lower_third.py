from __future__ import annotations

"""
Lower-third overlay generator (Phase 4 EPIC #4.3 — Drama Visual Assets).

Renders a transparent PNG with a semi-transparent bar + character name/role
text (e.g. "Mai (Chị dâu)"), meant to be composited via FFmpeg's `overlay`
filter during the "escalation" scene of a Drama video (see
video/drama_composer.py) — the same "pre-render a PNG, overlay once" pattern
the subtitle system already uses (video_composer.py's `_render_one_subtitle`),
reused here rather than duplicated.
"""

import logging
import os

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from video.video_composer import _load_font

logger = logging.getLogger(__name__)

BAR_HEIGHT_RATIO = 0.09      # bar height as a fraction of frame height
BAR_Y_RATIO = 0.72           # bar top position as a fraction of frame height
BAR_OPACITY = 160            # 0-255
NAME_FONTSIZE_RATIO = 0.032  # relative to frame height
TEXT_LEFT_MARGIN_RATIO = 0.06


def render_lower_third(name: str, role: str, width: int, height: int,
                       output_path: str) -> str | None:
    """Render a transparent WxH PNG: a semi-transparent bar + "Name (Role)" text.

    Returns `output_path` on success, or None if Pillow is unavailable, no
    usable font is found, or the name is empty — callers should skip the
    overlay entirely in that case rather than composite a blank/broken PNG.
    """
    if not name or not name.strip():
        logger.warning("render_lower_third: empty name — skipping overlay")
        return None

    try:
        from PIL import Image, ImageDraw
    except ImportError:
        logger.error("Pillow not installed — cannot render lower-third")
        return None

    text = f"{name.strip()} ({role.strip()})" if role and role.strip() else name.strip()

    fontsize = max(12, int(height * NAME_FONTSIZE_RATIO))
    font = _load_font(fontsize)
    if font is None:
        logger.error("No usable font found for lower-third")
        return None

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    bar_h = int(height * BAR_HEIGHT_RATIO)
    bar_y = int(height * BAR_Y_RATIO)
    draw.rectangle([(0, bar_y), (width, bar_y + bar_h)], fill=(0, 0, 0, BAR_OPACITY))

    bbox = draw.textbbox((0, 0), text, font=font)
    text_h = bbox[3] - bbox[1]
    x = int(width * TEXT_LEFT_MARGIN_RATIO)
    y = bar_y + (bar_h - text_h) // 2 - bbox[1]
    draw.text((x, y), text, font=font, fill=(255, 255, 255, 255))

    try:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        img.save(output_path, "PNG")
    except OSError as e:
        logger.error("Failed to save lower-third PNG: %s", e)
        return None
    return output_path
