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

_MAX_ATTEMPTS = 3
# How much of the model's actual reply to keep in logs / needs_review payloads
# when parsing fails. The old code discarded the response entirely, so a failure
# was indistinguishable between "truncated JSON", "model refused", and "model
# returned prose" (issue #82's three competing hypotheses) — keep a snippet so
# the real cause is visible without re-running.
_RESPONSE_SNIPPET_CHARS = 600

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


class _RewriteParseError(Exception):
    """The model replied, but the reply held no usable rewrite JSON.

    Distinct from anthropic.APIError (never reached the model) — this means we
    DID get text back but couldn't turn it into a dict, so the caller looks at
    the reply's ``stop_reason`` to tell truncation from a refusal/off-format
    answer.
    """


def _reply_text(message) -> str:
    """Return the first text block of a message, stripped ('' if none).

    Sturdier than ``message.content[0].text``: a leading non-text block or an
    empty ``content`` list (possible when a reply is truncated to nothing)
    would otherwise raise IndexError/AttributeError instead of failing as a
    plain parse error.
    """
    for block in getattr(message, "content", None) or []:
        text = getattr(block, "text", None)
        if text is not None:
            return text.strip()
    return ""


def _extract_json(text: str) -> dict:
    """Pull the rewrite JSON object out of a model reply.

    Handles raw JSON, ```json``-fenced JSON, and JSON trailed by prose. Raises
    ``_RewriteParseError`` when there is no complete object — notably a reply
    truncated before its closing brace (issue #82), which the caller then
    attributes to ``stop_reason == 'max_tokens'``.
    """
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass  # greedy match may have swept up unbalanced trailing prose
    start = text.find("{")
    if start != -1:
        try:
            obj, _ = json.JSONDecoder().raw_decode(text[start:])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    raise _RewriteParseError("no complete JSON object in reply")


def _handle_unparseable(story_id: int, got_reply: bool, stop_reason,
                        snippet: str) -> None:
    """Decide what to do with a story that never yielded a valid rewrite.

    - No model reply at all (transient API/rate errors): leave the story
      untouched and return None so a later run retries it.
    - Model replied but we never got valid JSON (truncation that survived
      escalation, a refusal, or persistent prose): mark it 'needs_review' with
      the raw reply saved + a Telegram alert, rather than silently re-burning
      Sonnet tokens on the same poison story every single day.
    """
    if not got_reply:
        logger.error(
            "Failed to rewrite story %d after %d attempts (no usable model reply)",
            story_id, _MAX_ATTEMPTS,
        )
        return None

    if stop_reason == "max_tokens":
        reason = ("model output kept hitting max_tokens (script too long or "
                  "model looping) — reply truncated before valid JSON")
    else:
        reason = (f"model reply was not valid JSON (stop_reason={stop_reason}) "
                  f"— likely a refusal or off-format answer")
    logger.error(
        "Failed to rewrite story %d after %d attempts: %s. Reply head: %r",
        story_id, _MAX_ATTEMPTS, reason, snippet[:200],
    )
    envelope = json.dumps(
        {"_rewrite_error": reason, "_stop_reason": stop_reason, "_raw_reply": snippet},
        ensure_ascii=False,
    )
    update_status(story_id, "needs_review", rewritten_content=envelope)
    _alert_validation_failure(story_id, [reason])
    return None


def rewrite_story(story_id: int) -> dict | None:
    """Rewrite one story into a Vietnamese script via Claude Sonnet.

    On success, stores the JSON result in `stories.rewritten_content` and
    sets status:
    - 'approved' — passed validate_rewrite(), ready for production (Phase 4).
    - 'needs_review' — failed validate_rewrite(); the (still-saved) output
      needs a human look. A Telegram alert is sent with the specific issues.

    On failure: a story the model never answered for is left untouched (returns
    None) so a later run retries it; a story the model answered but never
    parsed into valid JSON is flagged 'needs_review' (also returns None) — see
    `_handle_unparseable`.
    """
    story = get_story(story_id)
    if not story:
        logger.error("Story %d not found", story_id)
        return None

    prompt_template = load_prompt("drama", "rewriter")
    prompt = render(prompt_template, RAW_CONTENT=(story["raw_content"] or "")[:4000])

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    result = None
    max_tokens = config.DRAMA_REWRITER_MAX_TOKENS
    got_reply = False       # did the model ever answer? (transient error vs. bad content)
    last_stop_reason = None
    last_snippet = ""

    for attempt in range(_MAX_ATTEMPTS):
        try:
            message = client.messages.create(
                model="claude-sonnet-4-5",
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            log_token_usage("drama_rewriter", story_id, message)
            got_reply = True
            last_stop_reason = getattr(message, "stop_reason", None)
            response_text = _reply_text(message)
            last_snippet = response_text[:_RESPONSE_SNIPPET_CHARS]
            result = _extract_json(response_text)
            break
        except _RewriteParseError as e:
            if last_stop_reason == "max_tokens":
                # Truncated mid-JSON: the reply was cut off before its closing
                # brace. Retrying at the same size just truncates identically
                # (the exact 3x-identical-failure of issue #82), so escalate
                # the ceiling before the next attempt.
                boosted = int(max_tokens * 1.5)
                logger.warning(
                    "Attempt %d/%d: rewrite for story %d truncated at max_tokens=%d; "
                    "raising to %d and retrying. Reply head: %r",
                    attempt + 1, _MAX_ATTEMPTS, story_id, max_tokens, boosted,
                    last_snippet[:200],
                )
                max_tokens = boosted
            else:
                logger.warning(
                    "Attempt %d/%d: failed to parse rewrite for story %d "
                    "(stop_reason=%s): %s. Reply head: %r",
                    attempt + 1, _MAX_ATTEMPTS, story_id, last_stop_reason, e,
                    last_snippet[:200],
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
        return _handle_unparseable(story_id, got_reply, last_stop_reason, last_snippet)

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
    """Best-effort Telegram alert — a notifier hiccup must not crash the batch.

    rewrite_all_scored() loops over stories; letting a failed alert raise here
    would abort every remaining story, so swallow and log instead.
    """
    lines = "\n".join(f"  • {i}" for i in issues)
    try:
        from notifier.telegram_bot import send_alert
        send_alert(f"⚠️ Story #{story_id} rewrite cần review thủ công:\n{lines}")
    except Exception as e:  # noqa: BLE001 — notifier is non-critical
        logger.warning("Failed to send review alert for story %d: %s", story_id, e)


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
