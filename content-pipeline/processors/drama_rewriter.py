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
from storage.stories import get_pending, get_by_status, update_status, get_story

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3
# How much of the model's actual reply to keep in the needs_review DB payload
# when parsing fails (issue #82). Kept bounded so a runaway reply can't bloat
# the row. The FULL reply is still emitted to logs (DEBUG per attempt, and in
# the final error) so operators can see exactly what Sonnet returned without
# re-running — issue #84 flagged that the old `[:200]` snippet hid the cause.
_RESPONSE_SNIPPET_CHARS = 600

# Assistant-turn prefill: we seed the assistant reply with a single "{" so the
# model is forced to continue from an open JSON object (issue #84 root cause).
# The prompt already says "reply with JSON only", but Sonnet occasionally still
# emits a prose preamble ("Đây là kịch bản...") or wraps the JSON in commentary
# — a 200-token HTTP-200 reply that carries no parseable object, so all 3
# retries fail identically and the story yields 0 video. Prefilling makes a
# preamble structurally impossible: the first token the model can emit is the
# JSON body. This is the canonical Anthropic technique for guaranteed-JSON
# output and is supported on claude-sonnet-4-5. The "{" is NOT echoed back in
# the response content, so we prepend it before parsing (see rewrite_story).
# It composes with the #82 truncation handling untouched: a reply cut off at
# max_tokens still lacks its closing brace, so _extract_json raises and the
# ×1.5 escalation kicks in exactly as before.
_JSON_PREFILL = "{"

_REQUIRED_FIELDS = ("title", "hook", "script", "vn_commentary", "thumbnail_prompt", "tags")
_VN_COMMENTARY_MIN_WORDS = 200

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


def _script_length_verdict(word_count: int) -> tuple[str | None, str | None]:
    """Classify a script's word count against the two validation bands (issue #86).

    Pure function. Returns ``(blocking_issue, soft_note)`` where at most one is
    non-None:
    - ``blocking_issue``: below ``HARD_MIN`` or above ``HARD_MAX`` — a truncated
      stub or runaway/looping output. The caller rejects it to 'needs_review'.
    - ``soft_note``: inside the accept range but outside the ideal ``SOFT`` band.
      The caller still approves (it's a complete, real script) but logs the note
      so a persistently-short model is visible for prompt/threshold tuning.
    - both None: inside the ideal band.

    Reading the bounds from ``config`` (not module constants) keeps them
    env-overridable, matching DRAMA_REWRITER_MAX_TOKENS / DRAMA_SCORE_THRESHOLD.
    """
    hard_min = config.DRAMA_SCRIPT_HARD_MIN_WORDS
    hard_max = config.DRAMA_SCRIPT_HARD_MAX_WORDS
    soft_min = config.DRAMA_SCRIPT_SOFT_MIN_WORDS
    soft_max = config.DRAMA_SCRIPT_SOFT_MAX_WORDS

    if word_count < hard_min:
        return (
            f"script word count {word_count} below hard minimum {hard_min} "
            f"(likely a truncated/stub script, not a full story)",
            None,
        )
    if word_count > hard_max:
        return (
            f"script word count {word_count} above hard maximum {hard_max} "
            f"(likely runaway/looping output)",
            None,
        )
    if not (soft_min <= word_count <= soft_max):
        return (
            None,
            f"script word count {word_count} outside ideal {soft_min}-{soft_max} "
            f"but within accepted {hard_min}-{hard_max} — approving",
        )
    return (None, None)


def _hook_length_verdict(word_count: int) -> tuple[str | None, str | None]:
    """Classify a hook's word count — two bands, same design as the script check.

    Issue #99: the old single hard threshold (25 words) blocked a hook that ran
    ONE word over, sending an otherwise complete rewrite to 'needs_review' —
    which for stories is a dead end (no review UI; rewrite_all_scored skips
    them). A real 3-second hook is one short punchy line, and length is the
    cheap proxy we have for that — but "slightly long" and "a paragraph" are
    different failures:
    - <= SOFT_MAX: ideal → (None, None)
    - (SOFT_MAX, HARD_MAX]: still a hook, just wordy → soft note, approve
    - > HARD_MAX: a paragraph, structure went wrong → blocking
    """
    soft_max = config.DRAMA_HOOK_SOFT_MAX_WORDS
    hard_max = config.DRAMA_HOOK_HARD_MAX_WORDS

    if word_count > hard_max:
        return (
            f"hook has {word_count} words — a paragraph, not a ~3s line "
            f"(hard max {hard_max})",
            None,
        )
    if word_count > soft_max:
        return (
            None,
            f"hook has {word_count} words, over the ideal ~3s length "
            f"({soft_max}) but within accepted ({hard_max}) — approving",
        )
    return (None, None)


def validate_rewrite_verdict(result: dict) -> tuple[list[str], list[str]]:
    """Two-tier heuristic quality gate for a rewriter JSON result (issue #99).

    Pure function, no network — returns ``(blocking_issues, soft_notes)``:
    - ``blocking_issues``: the rewrite is broken/unusable → 'needs_review'
      (missing fields, script outside the accepted band, commentary too short,
      Western names / US-culture terms leaking through, bad tags, hook so long
      it's a paragraph).
    - ``soft_notes``: imperfect but production-worthy → the caller still
      approves and logs these for prompt/threshold tuning (script short/long
      of the ideal band, hook slightly over the ideal length).

    Checked (per phase-3-detailed.md EPIC #3.2 "Validation post-rewrite"):
    - all required fields present and non-empty
    - `script` word count within the accepted band [HARD_MIN, HARD_MAX]
      (issue #86: the ideal is [SOFT_MIN, SOFT_MAX] = 800-1200, but a script
      merely short/long of ideal is accepted, not blocked)
    - `vn_commentary` >= 200 words
    - `hook` is a short punchy line, not a paragraph (issue #99: two-band —
      slightly-long approves with a note, only a paragraph blocks)
    - no common Western name fragments / US-culture terms leaking through
      (scanned across title/script/vn_commentary AND the optional vn_reactions)
    - `tags` is a non-empty list

    `vn_reactions` (the localized community-reactions beat, issue #92 follow-up)
    is OPTIONAL — only stories carrying comments have it — so its absence never
    blocks; when present it's held to the same localization rules.
    """
    issues: list[str] = []
    notes: list[str] = []
    missing = [k for k in _REQUIRED_FIELDS if not result.get(k)]
    if missing:
        issues.append(f"missing/empty field(s): {missing}")
        return issues, notes  # other checks need these fields to make sense

    script = result["script"]
    blocking, soft = _script_length_verdict(len(script.split()))
    if blocking:
        issues.append(blocking)
    if soft:
        notes.append(soft)

    blocking, soft = _hook_length_verdict(len(result["hook"].split()))
    if blocking:
        issues.append(blocking)
    if soft:
        notes.append(soft)

    commentary_count = len(result["vn_commentary"].split())
    if commentary_count < _VN_COMMENTARY_MIN_WORDS:
        issues.append(
            f"vn_commentary only {commentary_count} words "
            f"(need >= {_VN_COMMENTARY_MIN_WORDS})"
        )

    # vn_reactions is OPTIONAL (only stories carrying comments have it), so it's
    # not a required field — but when present it must obey the same localization
    # rules (no Western names / US-culture terms / raw YTA-NTA verdicts leaking).
    reactions = result.get("vn_reactions") or ""
    combined = f"{result['title']} {script} {result['vn_commentary']} {reactions}".lower()

    for term in _FOREIGN_CULTURE_TERMS + _WESTERN_NAME_FRAGMENTS:
        if re.search(rf"\b{re.escape(term)}\b", combined):
            issues.append(f"contains disallowed term: {term!r}")

    for sym in _FOREIGN_CULTURE_SYMBOLS:
        if sym in combined:
            issues.append(f"contains disallowed symbol: {sym!r}")

    if not isinstance(result.get("tags"), list) or not result["tags"]:
        issues.append("tags must be a non-empty list")

    return issues, notes


def validate_rewrite(result: dict) -> list[str]:
    """Blocking issues only — thin wrapper kept for existing callers/tests.

    See `validate_rewrite_verdict` for the two-tier version (blocking vs. soft
    notes); an empty return here means the rewrite is approvable.
    """
    issues, _notes = validate_rewrite_verdict(result)
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


def _extract_rewrite_json(reply: str) -> dict:
    """Parse the rewrite object from a (possibly prefill-continued) reply.

    We seed the assistant turn with ``{`` (see ``_JSON_PREFILL`` /
    ``rewrite_story``); the API does not echo that prefill back, so the honored
    case is a reply that is the JSON *body* with no leading brace (e.g.
    ``"title": ...}``). But the prefill isn't guaranteed to be honored — a reply
    may still arrive as a complete object, possibly behind a prose preamble or a
    ```json fence, which ``_extract_json`` already handles.

    So try the reply as-is first (covers the prefill-ignored / full-object
    cases), then fall back to prepending the ``{`` (the honored-prefill case).
    Prepending unconditionally would corrupt a full-object-behind-a-prefix reply
    — the synthetic ``{`` makes ``raw_decode`` start on invalid input — which
    reintroduces the issue #84 failure in that very fallback path.
    """
    try:
        return _extract_json(reply)
    except _RewriteParseError:
        if reply.startswith(_JSON_PREFILL):
            raise  # already brace-led; prepending would only double it
        return _extract_json(_JSON_PREFILL + reply)


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
        "Failed to rewrite story %d after %d attempts: %s. Reply: %r",
        story_id, _MAX_ATTEMPTS, reason, snippet,
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
                messages=[
                    {"role": "user", "content": prompt},
                    # Prefill "{" — forces a pure-JSON continuation (issue #84).
                    {"role": "assistant", "content": _JSON_PREFILL},
                ],
            )
            log_token_usage("drama_rewriter", story_id, message)
            got_reply = True
            last_stop_reason = getattr(message, "stop_reason", None)
            # Log/save the RAW reply (what the model actually returned), not a
            # reconstructed variant — that's the truthful record for debugging.
            raw_reply = _reply_text(message)
            last_snippet = raw_reply[:_RESPONSE_SNIPPET_CHARS]
            # Full reply at DEBUG so the actual model output is recoverable
            # without a re-run (issue #84 #3). Kept off the default INFO path so
            # a healthy 800-1200 word script doesn't spam the log every run.
            logger.debug(
                "Story %d attempt %d/%d raw reply (stop_reason=%s): %r",
                story_id, attempt + 1, _MAX_ATTEMPTS, last_stop_reason, raw_reply,
            )
            # Reconstruct the "{" the prefill dropped, but only as a fallback so
            # a full object behind a prefix still parses (see _extract_rewrite_json).
            result = _extract_rewrite_json(raw_reply)
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
                    "raising to %d and retrying. Reply: %r",
                    attempt + 1, _MAX_ATTEMPTS, story_id, max_tokens, boosted,
                    last_snippet,
                )
                max_tokens = boosted
            else:
                logger.warning(
                    "Attempt %d/%d: failed to parse rewrite for story %d "
                    "(stop_reason=%s): %s. Reply: %r",
                    attempt + 1, _MAX_ATTEMPTS, story_id, last_stop_reason, e,
                    last_snippet,
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

    issues, soft_notes = validate_rewrite_verdict(result)
    rewritten_json = json.dumps(result, ensure_ascii=False)

    if issues:
        update_status(story_id, "needs_review", rewritten_content=rewritten_json)
        logger.warning("Story %d rewrite failed validation: %s", story_id, issues)
        _alert_validation_failure(story_id, issues)
    else:
        # Passed the gate. Soft notes (script short/long of ideal, hook a bit
        # wordy) don't block — but a model that keeps landing here is a signal
        # to tune the prompt or thresholds (issues #86/#99). Log only, no
        # Telegram: alerts are for stories needing a human, notes are not.
        for note in soft_notes:
            logger.info("Story %d approved with note: %s", story_id, note)
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


def revalidate_needs_review(limit: int | None = None) -> int:
    """Re-validate 'needs_review' drama stories against the CURRENT thresholds.

    Recovery path for issue #99: a story hard-blocked by a validation rule that
    was later relaxed (e.g. story 574's 26-word hook under the old 25-word hard
    limit) has no other way out — there is no story review UI, and
    rewrite_all_scored deliberately skips 'needs_review'. This re-runs
    `validate_rewrite_verdict` over the ALREADY-SAVED rewrite (zero AI calls,
    zero cost) and approves stories that now pass; still-blocked stories are
    left untouched, and unparseable-reply error envelopes (`_rewrite_error`,
    see _handle_unparseable) are skipped — they hold no rewrite to validate.

    Sweeps ALL stuck stories by default (Codex review, PR #100): get_by_status
    sorts newest-first, so any fixed page size would forever hide older rows
    behind still-blocked newer ones once the backlog exceeds it. A one-shot
    manual command over a small SQLite table doesn't need paging.

    Run manually after a threshold change: `python -m processors.drama_rewriter
    --revalidate`. Returns the number of stories approved.
    """
    stuck = get_by_status("needs_review", limit=limit, track="drama")
    recovered = 0
    for story in stuck:
        raw = story.get("rewritten_content")
        if not raw:
            continue
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(result, dict) or "_rewrite_error" in result:
            continue
        issues, soft_notes = validate_rewrite_verdict(result)
        if issues:
            logger.info("Story %d still blocked: %s", story["id"], issues)
            continue
        for note in soft_notes:
            logger.info("Story %d approved with note: %s", story["id"], note)
        update_status(story["id"], "approved")
        logger.info("Story %d re-validated OK — approved", story["id"])
        recovered += 1
    logger.info(
        "Re-validated %d needs_review drama stories, approved %d",
        len(stuck), recovered,
    )
    return recovered


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if "--revalidate" in sys.argv:
        revalidate_needs_review()
    else:
        rewrite_all_scored()
