from __future__ import annotations

"""
Drama Compiler — gom 3-5 story cùng theme thành 1 script video long-form
(Phase 3 EPIC #3.3). Chạy weekly (thứ 6) trên story `status='produced'`
(đã qua Phase 4 — sản xuất thành video riêng lẻ).
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
from storage.stories import get_by_status
from storage.compiled_videos import insert_compiled_video

logger = logging.getLogger(__name__)

MIN_STORIES_FOR_THEME = 3
MAX_STORIES_PER_COMPILATION = 5

# Derived from this codebase's own established narration rate (see
# video/script_generator.py: "long" AI videos target 800-1200 words for a
# 5-10 min video, i.e. ~120-160 words/minute). Using ~140 wpm:
#   8 min ≈ 1120 words, 15 min ≈ 2100 words.
TARGET_MIN_WORDS = 1100
TARGET_MAX_WORDS = 2100

_CHAPTER_MARKER_RE = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?\s+\S")


def detect_theme(candidate_stories: list[dict]) -> dict | None:
    """Find a theme shared by >= MIN_STORIES_FOR_THEME stories (Claude Sonnet).

    Returns {"theme": str, "story_ids": [...], "reason": str}, or None if no
    theme reaches the threshold or the call/parse fails after retries.
    """
    if len(candidate_stories) < MIN_STORIES_FOR_THEME:
        logger.info(
            "Only %d candidate stories (< %d) — skipping theme detection",
            len(candidate_stories), MIN_STORIES_FOR_THEME,
        )
        return None

    story_list = "\n".join(
        f"{s['id']}: {(s.get('title') or s['raw_content'] or '')[:100]}"
        for s in candidate_stories
    )
    prompt = render(load_prompt("drama", "theme_detect"), STORY_LIST=story_list)

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    for attempt in range(3):
        try:
            message = client.messages.create(
                model="claude-sonnet-4-5", max_tokens=500,
                messages=[{"role": "user", "content": prompt}],
            )
            log_token_usage("drama_theme_detect", f"{len(candidate_stories)}_stories", message)
            response_text = message.content[0].text.strip()
            json_match = re.search(r"\{.*\}", response_text, re.DOTALL)
            if not json_match:
                raise json.JSONDecodeError("No JSON object found", response_text, 0)
            result = json.loads(json_match.group())
            if not result.get("theme") or len(result.get("story_ids") or []) < MIN_STORIES_FOR_THEME:
                logger.info("No qualifying theme found: %s", result.get("reason", ""))
                return None
            return result
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(
                "Attempt %d/3: failed to parse theme detection response: %s", attempt + 1, e,
            )
            continue
        except anthropic.RateLimitError:
            wait = 2 ** (attempt + 1)
            logger.warning("Rate limited detecting theme, waiting %ds", wait)
            time.sleep(wait)
            continue
        except anthropic.APIError as e:
            logger.error("API error detecting theme: %s", e)
            return None
    logger.error("Failed to detect theme after 3 attempts")
    return None


def _story_script_block(story: dict) -> str:
    """Best-effort script text for a story: prefer the Vietnamese rewrite."""
    rewritten = story.get("rewritten_content")
    if isinstance(rewritten, str):
        try:
            rewritten = json.loads(rewritten)
        except json.JSONDecodeError:
            rewritten = None
    title = (rewritten or {}).get("title") or story.get("title") or f"Story {story['id']}"
    script = (rewritten or {}).get("script") or story.get("raw_content") or ""
    return f"--- Story #{story['id']}: {title} ---\n{script}"


def compile_long_form(selected_stories: list[dict], theme: str) -> dict | None:
    """Generate the long-form compiled script (intro/bridges/outro/chapters).

    Returns the parsed result dict, or None on failure after retries.
    """
    selected = selected_stories[:MAX_STORIES_PER_COMPILATION]
    stories_block = "\n\n".join(_story_script_block(s) for s in selected)

    prompt = render(
        load_prompt("drama", "longform"),
        STORY_COUNT=str(len(selected)), THEME=theme, STORIES_BLOCK=stories_block,
    )

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    for attempt in range(3):
        try:
            message = client.messages.create(
                model="claude-sonnet-4-5", max_tokens=4000,
                messages=[{"role": "user", "content": prompt}],
            )
            log_token_usage("drama_compiler", theme, message)
            response_text = message.content[0].text.strip()
            json_match = re.search(r"\{.*\}", response_text, re.DOTALL)
            if not json_match:
                raise json.JSONDecodeError("No JSON object found", response_text, 0)
            result = json.loads(json_match.group())
            _validate_compiled_script(result)
            return result
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(
                "Attempt %d/3: failed to parse/validate compiled script: %s", attempt + 1, e,
            )
            continue
        except anthropic.RateLimitError:
            wait = 2 ** (attempt + 1)
            logger.warning("Rate limited compiling long-form, waiting %ds", wait)
            time.sleep(wait)
            continue
        except anthropic.APIError as e:
            logger.error("API error compiling long-form script: %s", e)
            return None
    logger.error("Failed to compile long-form script after 3 attempts")
    return None


def _validate_compiled_script(result: dict) -> None:
    """Raise ValueError if `result` doesn't look like a usable compiled script."""
    required = ("intro", "outro", "chapters", "full_script")
    missing = [k for k in required if not result.get(k)]
    if missing:
        raise ValueError(f"missing/empty field(s): {missing}")

    if not isinstance(result["chapters"], list) or not result["chapters"]:
        raise ValueError("chapters must be a non-empty list")
    for chapter in result["chapters"]:
        if not _CHAPTER_MARKER_RE.match(chapter):
            raise ValueError(f"chapter marker not in 'MM:SS Title' format: {chapter!r}")

    word_count = len(result["full_script"].split())
    if not (TARGET_MIN_WORDS <= word_count <= TARGET_MAX_WORDS):
        raise ValueError(
            f"full_script word count {word_count} outside "
            f"{TARGET_MIN_WORDS}-{TARGET_MAX_WORDS} (~8-15 min)"
        )


def run_weekly_compilation() -> int | None:
    """Weekly job (Friday): detect a theme among produced Drama stories and
    compile a long-form script. Returns the new compiled_videos id, or None
    if no theme reached the threshold or compilation failed.
    """
    produced = get_by_status("produced", limit=50, track="drama")
    theme_result = detect_theme(produced)
    if theme_result is None:
        return None

    selected_ids = set(theme_result["story_ids"])
    selected = [s for s in produced if s["id"] in selected_ids]
    if len(selected) < MIN_STORIES_FOR_THEME:
        logger.warning(
            "Theme detection returned story_ids not present in the produced set — skipping"
        )
        return None

    compiled = compile_long_form(selected, theme_result["theme"])
    if compiled is None:
        return None

    video_id = insert_compiled_video(
        theme=theme_result["theme"],
        story_ids=[s["id"] for s in selected],
        script=compiled["full_script"],
        chapter_markers=compiled["chapters"],
    )
    logger.info(
        "Compiled long-form video %d for theme %r (%d stories)",
        video_id, theme_result["theme"], len(selected),
    )
    return video_id


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_weekly_compilation()
