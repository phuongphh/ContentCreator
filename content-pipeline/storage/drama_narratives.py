"""Persistent 8/1/1 rotation for newly written drama_youtube story scripts.

The rewriter holds a per-database file lock across selection and generation.
Commit the sequence, story assignment and draft together; API/parse failures
consume no slot, and rewriting an assigned story keeps its original format.
"""
from __future__ import annotations

import json

from storage.database import get_connection

_STATE_KEY = "drama_youtube.narrative_sequence"
_METADATA_KEY = "drama_youtube_narrative"


def select_narrative(story: dict) -> dict | None:
    if story.get("track") != "drama" or story.get("destination") not in (
        None, "", "drama_youtube",
    ):
        return None
    metadata = story.get("metadata") or {}
    if isinstance(metadata, dict) and _METADATA_KEY in metadata:
        return metadata[_METADATA_KEY]
    conn = get_connection()
    try:
        row = conn.execute("SELECT value FROM pipeline_state WHERE key = ?",
                           (_STATE_KEY,)).fetchone()
        sequence = int(row["value"]) + 1 if row else 1
    finally:
        conn.close()
    position = (sequence - 1) % 10 + 1
    return {"sequence": sequence,
            "format": {5: "conversation", 10: "debate"}.get(position, "narrative")}


def save_narrative(story_id: int, status: str, result: dict, assignment: dict) -> None:
    """Save a structured draft (including needs_review), advancing only once."""
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT metadata FROM stories WHERE id = ?",
                           (story_id,)).fetchone()
        if row is None:
            raise ValueError(f"Story {story_id} no longer exists")
        metadata = json.loads(row["metadata"]) if row["metadata"] else {}
        if not isinstance(metadata, dict):
            raise ValueError(f"Story {story_id} metadata must be an object")
        previous = metadata.get(_METADATA_KEY)
        if previous is None:
            cursor = conn.execute("SELECT value FROM pipeline_state WHERE key = ?",
                                  (_STATE_KEY,)).fetchone()
            expected = int(cursor["value"]) + 1 if cursor else 1
            if assignment["sequence"] != expected:
                raise ValueError("Drama narrative sequence changed during rewrite")
            metadata[_METADATA_KEY] = assignment
            conn.execute(
                "INSERT INTO pipeline_state (key, value, updated_at) "
                "VALUES (?, ?, CURRENT_TIMESTAMP) ON CONFLICT(key) DO UPDATE "
                "SET value = excluded.value, updated_at = CURRENT_TIMESTAMP",
                (_STATE_KEY, str(assignment["sequence"])),
            )
        elif previous != assignment:
            raise ValueError("Cannot change an assigned drama narrative format")
        conn.execute(
            "UPDATE stories SET status = ?, rewritten_content = ?, metadata = ? "
            "WHERE id = ?",
            (status, json.dumps(result, ensure_ascii=False),
             json.dumps(metadata, ensure_ascii=False), story_id),
        )
        conn.commit()
    finally:
        conn.close()
