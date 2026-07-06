from __future__ import annotations

"""
Storage helper cho bảng `compiled_videos` (Phase 3 EPIC #3.3 — Drama Compiler).

Yêu cầu migration 004_compiled_videos (xem storage/migrate.py).
"""

import json
import logging
from typing import Optional

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from storage.database import get_connection

logger = logging.getLogger(__name__)


def insert_compiled_video(theme: str, story_ids: list[int], script: str,
                          chapter_markers: list[str]) -> int:
    """Insert a new compiled long-form video record. Returns its id."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO compiled_videos (theme, story_ids, script, chapter_markers) "
            "VALUES (?, ?, ?, ?)",
            (
                theme,
                json.dumps(story_ids),
                script,
                json.dumps(chapter_markers, ensure_ascii=False),
            ),
        )
        conn.commit()
        logger.info(
            "Inserted compiled video id=%d theme=%r (%d stories)",
            cursor.lastrowid, theme, len(story_ids),
        )
        return cursor.lastrowid
    finally:
        conn.close()


def get_compiled_video(video_id: int) -> Optional[dict]:
    """Fetch a compiled video by id, with story_ids/chapter_markers parsed back to lists."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM compiled_videos WHERE id = ?", (video_id,)
        ).fetchone()
    finally:
        conn.close()
    return _row_to_dict(row)


def get_recent_compiled_videos(limit: int = 10) -> list[dict]:
    """Most recent compiled videos, newest first."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM compiled_videos ORDER BY created_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_dict(r) for r in rows]


def _row_to_dict(row) -> Optional[dict]:
    if row is None:
        return None
    d = dict(row)
    d["story_ids"] = json.loads(d["story_ids"]) if d.get("story_ids") else []
    d["chapter_markers"] = json.loads(d["chapter_markers"]) if d.get("chapter_markers") else []
    return d
