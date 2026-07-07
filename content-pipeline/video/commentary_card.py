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
CARD_MIN_FONTSIZE_RATIO = 0.02       # floor while shrinking to fit long commentary
CARD_FONTSIZE_SHRINK_STEP = 0.9
CARD_BG_COLOR = (13, 27, 42, 235)   # near-opaque dark navy tint
CARD_TEXT_COLOR = (255, 255, 255, 255)
CARD_TEXT_WIDTH_RATIO = 0.82        # max text width as a fraction of frame width
CARD_TEXT_HEIGHT_RATIO = 0.86       # max text block height as a fraction of frame height


def _fit_commentary_text(text: str, width: int, height: int):
    """Find the largest font size (down to a floor) whose wrapped `text` fits
    within the card's height.

    `vn_commentary` (Phase 3's rewriter validates it at >=200 words) is much
    longer than a lower-third label — at a fixed font size it can wrap into
    more lines than the frame is tall, silently pushing the earliest lines to
    a negative y (drawn off-canvas, invisible) while the card still "renders
    successfully". Shrinking the font until the whole block fits keeps all of
    the commentary visible instead of losing its start.

    Returns (font, lines, line_h, line_spacing), or None if no usable font.
    """
    max_text_width = int(width * CARD_TEXT_WIDTH_RATIO)
    max_text_height = int(height * CARD_TEXT_HEIGHT_RATIO)
    fontsize = max(16, int(height * CARD_FONTSIZE_RATIO))
    min_fontsize = max(12, int(height * CARD_MIN_FONTSIZE_RATIO))

    from PIL import Image, ImageDraw
    tmp = Image.new("RGBA", (1, 1))
    measure = ImageDraw.Draw(tmp)

    best = None
    while True:
        font = _load_font(fontsize)
        if font is None:
            return None
        lines = _wrap_text(text, font, max_text_width)
        bbox = measure.textbbox((0, 0), "Ag", font=font)
        line_h = bbox[3] - bbox[1]
        line_spacing = int(line_h * 0.4)
        total_h = len(lines) * line_h + max(0, len(lines) - 1) * line_spacing
        best = (font, lines, line_h, line_spacing)
        if total_h <= max_text_height or fontsize <= min_fontsize:
            if total_h > max_text_height:
                logger.warning(
                    "Commentary text still exceeds card height at minimum "
                    "font size (%dpx) — rendering best-effort", fontsize,
                )
            return best
        fontsize = max(min_fontsize, int(fontsize * CARD_FONTSIZE_SHRINK_STEP))


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

    fit = _fit_commentary_text(text.strip(), width, height)
    if fit is None:
        logger.error("No usable font found for commentary card")
        return None
    font, lines, line_h, line_spacing = fit

    img = Image.new("RGBA", (width, height), CARD_BG_COLOR)
    draw = ImageDraw.Draw(img)

    total_h = len(lines) * line_h + max(0, len(lines) - 1) * line_spacing
    top = max(0, (height - total_h) // 2)

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
