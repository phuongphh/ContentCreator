from __future__ import annotations

"""
Shared token-usage logging for AI calls (Phase 3 EPIC #3.2; cost persistence
added Phase 6).

Logs raw input/output token counts per call — not a $ estimate, since
hardcoding per-model pricing here would silently go stale as prices change.
Token counts alone are enough to spot a run that's unexpectedly expensive; the
$ conversion is a display-time overlay in analytics/pricing.py.

Phase 6: besides logging, we now PERSIST each call's raw tokens into
`cost_logs` (best-effort) so the analytics cost tab / weekly retro have real
data. This makes phase-6-detailed.md's assumption that cost_logs "đã ghi từ
Phase 3, 4" actually true. Persistence never raises into the caller — a DB
without migration 007 just skips the write.
"""

import logging

logger = logging.getLogger(__name__)


def log_token_usage(label: str, story_id, message,
                    service: str = "anthropic", ref_type: str | None = "story") -> None:
    """Log + persist input/output token counts from an Anthropic Message response.

    `story_id` may be any reference (int story/article id, or a synthetic key
    like "3_stories" for batch calls) — stored verbatim as cost_logs.ref_id.
    """
    usage = getattr(message, "usage", None)
    if usage is None:
        return
    input_tokens = getattr(usage, "input_tokens", 0)
    output_tokens = getattr(usage, "output_tokens", 0)
    model = getattr(message, "model", None)
    # %s (not %d): tolerates a non-int usage object (e.g. a test double)
    # without crashing logging's lazy message formatting.
    logger.info(
        "%s story=%s tokens: input=%s output=%s",
        label, story_id, input_tokens, output_tokens,
    )

    # Persist raw tokens for cost analytics. Best-effort: never let a logging
    # helper break the pipeline (e.g. token counts are test doubles, or the DB
    # predates migration 007).
    try:
        from storage.cost_logs import record_cost
        record_cost(
            service=service, model=model, label=label,
            input_tokens=int(input_tokens), output_tokens=int(output_tokens),
            ref_type=ref_type, ref_id=story_id,
        )
    except Exception as e:  # includes non-int test doubles → int() TypeError
        logger.debug("cost persistence skipped for %s (non-fatal): %s", label, e)
