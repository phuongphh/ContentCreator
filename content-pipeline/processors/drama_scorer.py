from __future__ import annotations

"""
Drama Rubric Scorer — chấm story Drama theo 6 tiêu chí bằng Claude Haiku
(Phase 3 EPIC #3.1).

Tiêu chí: HOOK_3S, STAKES, TWIST, LOCALIZABLE, COMMENT_BAIT, SAFE (mỗi cái
0 hoặc 1). `total` LUÔN được tính lại từ 6 trường boolean phía server —
không tin vào con số "total" model tự báo cáo, vì LLM occasionally tính
sai tổng đơn giản trong JSON output; validate + tự cộng loại bỏ cả lớp lỗi
đó. Story `safe=0` luôn bị loại dù `total` cao (xem `phase-3-detailed.md`).
"""

import json
import logging
import re
import time

import anthropic

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
from processors.prompt_loader import load_prompt, render
from processors.ai_usage import log_token_usage
from storage.stories import get_pending, update_status, get_story

logger = logging.getLogger(__name__)

_RUBRIC_KEYS = ("hook_3s", "stakes", "twist", "localizable", "comment_bait", "safe")


def _validate_and_normalize_rubric(result: dict) -> dict:
    """Validate the 6 rubric fields and recompute `total` deterministically.

    Raises:
        ValueError: nếu thiếu field hoặc field không phải 0/1.
    """
    missing = [k for k in _RUBRIC_KEYS if k not in result]
    if missing:
        raise ValueError(f"Missing rubric keys: {missing}")
    for k in _RUBRIC_KEYS:
        if result[k] not in (0, 1):
            raise ValueError(f"Rubric field {k!r} must be 0 or 1, got {result[k]!r}")
    result["total"] = sum(result[k] for k in _RUBRIC_KEYS)
    result.setdefault("reason", "")
    return result


def score_story(story_id: int) -> dict | None:
    """Score one story via Claude Haiku's 6-criteria rubric.

    Updates the story's status: 'rejected' if it fails (safe=0 or total
    below config.DRAMA_SCORE_THRESHOLD), otherwise stays 'pending' — ready
    for the rewriter (Phase 3 EPIC #3.2) — with `rubric_score` recorded.

    Returns the parsed+normalized rubric dict, or None on failure (story
    left untouched so a later run can retry).
    """
    story = get_story(story_id)
    if not story:
        logger.error("Story %d not found", story_id)
        return None

    prompt_template = load_prompt("drama", "scorer")
    # Truncate like ai_scorer.py/ai_analyzer.py do — keeps token cost bounded
    # for the (rare) unusually long Reddit post.
    prompt = render(prompt_template, RAW_CONTENT=(story["raw_content"] or "")[:4000])

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    result = None

    for attempt in range(3):
        try:
            message = client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
            )
            log_token_usage("drama_scorer", story_id, message)
            response_text = message.content[0].text.strip()
            json_match = re.search(r"\{.*\}", response_text, re.DOTALL)
            if not json_match:
                raise json.JSONDecodeError("No JSON object found", response_text, 0)
            result = _validate_and_normalize_rubric(json.loads(json_match.group()))
            break
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(
                "Attempt %d/3: failed to parse rubric for story %d: %s",
                attempt + 1, story_id, e,
            )
            continue
        except anthropic.RateLimitError:
            wait = 2 ** (attempt + 1)
            logger.warning("Rate limited scoring story %d, waiting %ds", story_id, wait)
            time.sleep(wait)
            continue
        except anthropic.APIError as e:
            logger.error("API error scoring story %d: %s", story_id, e)
            return None

    if result is None:
        logger.error("Failed to score story %d after 3 attempts", story_id)
        return None

    total = result["total"]
    passes = result["safe"] == 1 and total >= config.DRAMA_SCORE_THRESHOLD
    if passes:
        update_status(story_id, "pending", rubric_score=total)
        logger.info("Story %d scored %d/6 — passes, ready for rewrite", story_id, total)
    else:
        update_status(story_id, "rejected", rubric_score=total)
        logger.info(
            "Story %d scored %d/6 (safe=%s) — rejected", story_id, total, result["safe"],
        )

    return result


def score_all_pending(limit: int = 20) -> int:
    """Score every pending drama story that hasn't been scored yet.

    Returns the count of stories successfully scored (a story that gets
    scored and then rejected still counts — it was successfully processed).
    """
    candidates = [
        s for s in get_pending(limit=limit, track="drama")
        if s.get("rubric_score") is None
    ]
    scored = 0
    for story in candidates:
        if score_story(story["id"]) is not None:
            scored += 1
    logger.info("Scored %d/%d pending drama stories", scored, len(candidates))
    return scored


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    score_all_pending()
