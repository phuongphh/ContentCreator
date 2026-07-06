from __future__ import annotations

"""
Drama Rewriter — Việt hoá story Reddit/VN-seed bằng Claude Sonnet (Phase 3
EPIC #3.2). Module quan trọng nhất của Drama track: mỗi story được kể lại
ngôi thứ nhất, nhân vật/bối cảnh chuyển hẳn sang Việt Nam, và thêm đoạn
"bình luận góc nhìn Việt" (≥20% thời lượng) — giá trị chuyển đổi (transformative)
không có trong bản gốc.
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

_REQUIRED_FIELDS = ("title", "hook", "script", "vn_commentary", "thumbnail_prompt", "tags")
_SCRIPT_MIN_WORDS = 800
_SCRIPT_MAX_WORDS = 1200
_VN_COMMENTARY_MIN_WORDS = 200
# Structure heuristic ("Hook 3s → Setup → Escalation → Twist → Reflection"):
# a real 3-second hook is one short punchy line, not a paragraph. Can't
# heuristically verify the other 4 structure beats without real NLP, but a
# hook that's suspiciously long is a good, cheap signal something went wrong.
_HOOK_MAX_WORDS = 25

# Heuristic guard against the 2 failure modes called out in
# phase-3-detailed.md's risk section: half-Western character names (e.g.
# "Linh Smith") and US-culture details leaking into a story meant to be set
# entirely in Vietnam.
_FOREIGN_CULTURE_TERMS = ("mall", "prom", "thanksgiving", "dollar", "dollars", "usd")
_FOREIGN_CULTURE_SYMBOLS = ("$",)
_WESTERN_NAME_FRAGMENTS = (
    "smith", "johnson", "williams", "brown", "jones", "miller", "davis",
    "john", "mike", "michael", "sarah", "jessica", "james", "robert",
    "david", "mary", "jennifer", "linda", "susan", "karen",
)


def validate_rewrite(result: dict) -> list[str]:
    """Heuristic quality gate for a rewriter JSON result.

    Pure function, no network — returns a list of human-readable issues;
    an empty list means the rewrite passes. Checked (per phase-3-detailed.md
    EPIC #3.2 "Validation post-rewrite"):
    - all required fields present and non-empty
    - `script` word count in [800, 1200]
    - `vn_commentary` >= 200 words
    - `hook` is a short punchy line, not a paragraph (structure heuristic)
    - no common Western name fragments / US-culture terms leaking through
    - `tags` is a non-empty list
    """
    issues = []
    missing = [k for k in _REQUIRED_FIELDS if not result.get(k)]
    if missing:
        issues.append(f"missing/empty field(s): {missing}")
        return issues  # other checks need these fields to make sense

    script = result["script"]
    word_count = len(script.split())
    if not (_SCRIPT_MIN_WORDS <= word_count <= _SCRIPT_MAX_WORDS):
        issues.append(
            f"script word count {word_count} outside "
            f"{_SCRIPT_MIN_WORDS}-{_SCRIPT_MAX_WORDS}"
        )

    hook_word_count = len(result["hook"].split())
    if hook_word_count > _HOOK_MAX_WORDS:
        issues.append(
            f"hook has {hook_word_count} words — expected a short ~3s line "
            f"(<= {_HOOK_MAX_WORDS})"
        )

    commentary_count = len(result["vn_commentary"].split())
    if commentary_count < _VN_COMMENTARY_MIN_WORDS:
        issues.append(
            f"vn_commentary only {commentary_count} words "
            f"(need >= {_VN_COMMENTARY_MIN_WORDS})"
        )

    combined = f"{result['title']} {script} {result['vn_commentary']}".lower()

    for term in _FOREIGN_CULTURE_TERMS + _WESTERN_NAME_FRAGMENTS:
        if re.search(rf"\b{re.escape(term)}\b", combined):
            issues.append(f"contains disallowed term: {term!r}")

    for sym in _FOREIGN_CULTURE_SYMBOLS:
        if sym in combined:
            issues.append(f"contains disallowed symbol: {sym!r}")

    if not isinstance(result.get("tags"), list) or not result["tags"]:
        issues.append("tags must be a non-empty list")

    return issues


def rewrite_story(story_id: int) -> dict | None:
    """Rewrite one story into a Vietnamese script via Claude Sonnet.

    On success, stores the JSON result in `stories.rewritten_content` and
    sets status:
    - 'approved' — passed validate_rewrite(), ready for production (Phase 4).
    - 'needs_review' — failed validate_rewrite(); the (still-saved) output
      needs a human look. A Telegram alert is sent with the specific issues.

    Returns the parsed rewrite dict, or None on failure (story left
    untouched so a later run can retry).
    """
    story = get_story(story_id)
    if not story:
        logger.error("Story %d not found", story_id)
        return None

    prompt_template = load_prompt("drama", "rewriter")
    prompt = render(prompt_template, RAW_CONTENT=(story["raw_content"] or "")[:4000])

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    result = None

    for attempt in range(3):
        try:
            message = client.messages.create(
                model="claude-sonnet-4-5",
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}],
            )
            log_token_usage("drama_rewriter", story_id, message)
            response_text = message.content[0].text.strip()
            json_match = re.search(r"\{.*\}", response_text, re.DOTALL)
            if not json_match:
                raise json.JSONDecodeError("No JSON object found", response_text, 0)
            result = json.loads(json_match.group())
            break
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(
                "Attempt %d/3: failed to parse rewrite for story %d: %s",
                attempt + 1, story_id, e,
            )
            continue
        except anthropic.RateLimitError:
            wait = 2 ** (attempt + 1)
            logger.warning("Rate limited rewriting story %d, waiting %ds", story_id, wait)
            time.sleep(wait)
            continue
        except anthropic.APIError as e:
            logger.error("API error rewriting story %d: %s", story_id, e)
            return None

    if result is None:
        logger.error("Failed to rewrite story %d after 3 attempts", story_id)
        return None

    issues = validate_rewrite(result)
    rewritten_json = json.dumps(result, ensure_ascii=False)

    if issues:
        update_status(story_id, "needs_review", rewritten_content=rewritten_json)
        logger.warning("Story %d rewrite failed validation: %s", story_id, issues)
        _alert_validation_failure(story_id, issues)
    else:
        update_status(story_id, "approved", rewritten_content=rewritten_json)
        logger.info("Story %d rewritten and validated OK", story_id)

    return result


def _alert_validation_failure(story_id: int, issues: list[str]) -> None:
    from notifier.telegram_bot import send_alert
    lines = "\n".join(f"  • {i}" for i in issues)
    send_alert(f"⚠️ Story #{story_id} rewrite cần review thủ công:\n{lines}")


def rewrite_all_scored(limit: int = 10) -> int:
    """Rewrite every scored-and-passing drama story not yet rewritten.

    Returns the count successfully processed (a rewrite that fails
    validation and lands in 'needs_review' still counts as processed).
    """
    candidates = [
        s for s in get_pending(limit=limit, track="drama")
        if s.get("rubric_score") is not None and not s.get("rewritten_content")
    ]
    done = 0
    for story in candidates:
        if rewrite_story(story["id"]) is not None:
            done += 1
    logger.info("Rewrote %d/%d scored drama stories", done, len(candidates))
    return done


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    rewrite_all_scored()
