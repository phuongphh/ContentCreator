from __future__ import annotations

"""Drama YouTube publish notification format tag (issue #130).

Reads the ORIGINAL persisted narrative assignment (stories.rewritten_content
JSON key ``narrative_format``) through videos.story_id. Never reclassifies.
Anything missing/invalid/unknown safely maps to ``normal``.
"""

import json
import logging

logger = logging.getLogger(__name__)

DEFAULT_FORMAT = "normal"
_FORMAT_MAP = {
    "narrative": "normal",
    "normal": "normal",
    "conversation": "conversation",
    "debate": "debate",
}


def normalize_format(value) -> str:
    """Map a persisted narrative_format value to normal|conversation|debate."""
    if isinstance(value, str):
        return _FORMAT_MAP.get(value.strip().lower(), DEFAULT_FORMAT)
    return DEFAULT_FORMAT


def format_from_story(story) -> str:
    """Read narrative_format from a story dict's rewritten_content JSON."""
    if not isinstance(story, dict):
        return DEFAULT_FORMAT
    content = story.get("rewritten_content")
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (ValueError, TypeError):
            return DEFAULT_FORMAT
    if not isinstance(content, dict):
        return DEFAULT_FORMAT
    return normalize_format(content.get("narrative_format"))


def get_video_narrative_format(video) -> str:
    """Format of the video's single source story; normal on any problem."""
    try:
        story_id = (video or {}).get("story_id")
        if not story_id:
            return DEFAULT_FORMAT
        from storage.stories import get_story
        return format_from_story(get_story(int(story_id)))
    except Exception as e:  # never suppress the notification
        logger.warning("narrative format lookup failed (default normal): %s", e)
        return DEFAULT_FORMAT
