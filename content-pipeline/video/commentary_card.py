from __future__ import annotations

"""
VN Commentary Card (Phase 4 EPIC #4.2/#4.3 — Drama Video Production).

Full-screen text card used as the background for the "vn_commentary_overlay"
Drama scene — the ≥20% "góc nhìn Việt" segment from processors/drama_rewriter.py
gets its own distinct visual treatment (solid tinted card + large centered
text) instead of looking like a regular subtitle over b-roll, so viewers can
tell "this part is commentary, not the story."
"""

import logging
import os

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from video.video_composer import _load_font, _wrap_text

logger = logging.getLogger(__name__)

CARD_FONTSIZE_RATIO = 0.045
CARD_BG_COLOR = (13, 27, 42, 235)   # near-opaque dark navy tint
CARD_TEXT_COLOR = (255, 255, 255, 255)
CARD_TEXT_WIDTH_RATIO = 0.82        # max text width as a fraction of frame width


def render_commentary_card(text: str, width: int, height: int,
                           output_path: str) -> str | None:
    """Render a full-screen semi-opaque card with centered, wrapped text.

    Returns `output_path` on success, or None if Pillow/font is unavailable
    or the text is empty.
    """
    if not text or not text.strip():
        logger.warning("render_commentary_card: empty text — skipping")
        return None

    try:
        from PIL import Image, ImageDraw
    except ImportError:
        logger.error("Pillow not installed — cannot render commentary card")
        return None

    fontsize = max(16, int(height * CARD_FONTSIZE_RATIO))
    font = _load_font(fontsize)
    if font is None:
        logger.error("No usable font found for commentary card")
        return None

    img = Image.new("RGBA", (width, height), CARD_BG_COLOR)
    draw = ImageDraw.Draw(img)

    max_text_width = int(width * CARD_TEXT_WIDTH_RATIO)
    lines = _wrap_text(text.strip(), font, max_text_width)

    sample_bbox = draw.textbbox((0, 0), "Ag", font=font)
    line_h = sample_bbox[3] - sample_bbox[1]
    line_spacing = int(line_h * 0.4)
    total_h = len(lines) * line_h + (len(lines) - 1) * line_spacing
    top = (height - total_h) // 2

    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        line_w = bbox[2] - bbox[0]
        x = (width - line_w) // 2
        y = top + i * (line_h + line_spacing)
        draw.text((x, y), line, font=font, fill=CARD_TEXT_COLOR)

    try:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        img.save(output_path, "PNG")
    except OSError as e:
        logger.error("Failed to save commentary card PNG: %s", e)
        return None
    return output_path
