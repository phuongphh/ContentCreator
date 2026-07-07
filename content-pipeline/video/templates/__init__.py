from __future__ import annotations

"""Scene template registry (Phase 4 EPIC #4.2) — load_template(track, format)."""

from .ai import AI_SHORTS_TEMPLATE
from .drama import DRAMA_SHORTS_TEMPLATE

_TEMPLATES = {
    ("ai", "shorts"): AI_SHORTS_TEMPLATE,
    ("drama", "shorts"): DRAMA_SHORTS_TEMPLATE,
}


def load_template(track: str, format: str = "shorts") -> dict:
    """Look up a scene template by (track, format).

    Raises:
        ValueError: if no template is registered for that combination.
    """
    key = (track, format)
    if key not in _TEMPLATES:
        raise ValueError(f"No template for track={track!r} format={format!r}")
    return _TEMPLATES[key]
