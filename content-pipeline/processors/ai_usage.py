from __future__ import annotations

"""
Shared token-usage logging for Drama processors (Phase 3 EPIC #3.2).

Logs raw input/output token counts per call — not a $ estimate, since
hardcoding per-model pricing here would silently go stale as prices change.
Check the current Anthropic pricing page if you want a cost figure; token
counts alone are enough to spot a run that's unexpectedly expensive.
"""

import logging

logger = logging.getLogger(__name__)


def log_token_usage(label: str, story_id: int, message) -> None:
    """Log input/output token counts from an Anthropic Message response."""
    usage = getattr(message, "usage", None)
    if usage is None:
        return
    # %s (not %d): tolerates a non-int usage object (e.g. a test double)
    # without crashing logging's lazy message formatting.
    logger.info(
        "%s story=%s tokens: input=%s output=%s",
        label, story_id,
        getattr(usage, "input_tokens", 0), getattr(usage, "output_tokens", 0),
    )
