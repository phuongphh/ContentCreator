from __future__ import annotations

"""
Storage helper cho bảng `ab_runs` (Phase 3 EPIC #3.4 — A/B harness).

Yêu cầu migration 005_ab_runs (xem storage/migrate.py).
"""

import logging
from typing import Optional

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from storage.database import get_connection

logger = logging.getLogger(__name__)


def record_run(experiment: str, version: str, story_id: Optional[int],
               heuristic_score: Optional[float]) -> int:
    """Record one A/B run. Returns the new row id."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO ab_runs (experiment, version, story_id, heuristic_score) "
            "VALUES (?, ?, ?, ?)",
            (experiment, version, story_id, heuristic_score),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_runs(experiment: str) -> list[dict]:
    """All recorded runs for `experiment`, oldest first."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM ab_runs WHERE experiment = ? ORDER BY id", (experiment,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]
