from __future__ import annotations

"""
Storage helper cho bảng `channel_metrics` (Phase 6 — Analytics & Iteration).

Snapshot cấp KÊNH (subscriber, subscribers_gained, views). video_metrics một
mình không suy ra được "kênh tăng bao nhiêu sub tuần này" — weekly retro cần
số này (phase-6-detailed.md §3.6 "Sub growth từng kênh").

Yêu cầu migration 007_analytics.
"""

import logging
from datetime import datetime
from typing import Optional

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from storage.database import get_connection

logger = logging.getLogger(__name__)

_METRIC_FIELDS = ("subscribers", "subscribers_gained", "views")


def _today() -> str:
    return datetime.now().date().isoformat()


def upsert_channel_metric(channel_key: str, platform: str,
                          snapshot_date: Optional[str] = None, **metrics) -> int:
    """Ghi/ghi đè snapshot cấp kênh. Idempotent theo (channel_key, snapshot_date)."""
    unknown = set(metrics) - set(_METRIC_FIELDS)
    if unknown:
        raise ValueError(f"upsert_channel_metric: unknown field(s) {sorted(unknown)}")
    snapshot_date = snapshot_date or _today()

    cols = ["channel_key", "platform", "snapshot_date"]
    vals = [channel_key, platform, snapshot_date]
    for field in _METRIC_FIELDS:
        if field in metrics:
            cols.append(field)
            vals.append(metrics[field])

    placeholders = ", ".join("?" * len(cols))
    update_cols = [c for c in cols if c not in ("channel_key", "snapshot_date")]
    update_clause = ", ".join(f"{c} = excluded.{c}" for c in update_cols)

    conn = get_connection()
    try:
        conn.execute(
            f"INSERT INTO channel_metrics ({', '.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(channel_key, snapshot_date) DO UPDATE SET {update_clause}",
            vals,
        )
        conn.commit()
        row = conn.execute(
            "SELECT id FROM channel_metrics WHERE channel_key = ? AND snapshot_date = ?",
            (channel_key, snapshot_date),
        ).fetchone()
        return int(row["id"]) if row else 0
    finally:
        conn.close()


def get_range(channel_key: str, since: Optional[str] = None) -> list[dict]:
    """Snapshot của 1 kênh, cũ → mới, lọc snapshot_date >= since nếu truyền."""
    conn = get_connection()
    try:
        if since:
            rows = conn.execute(
                "SELECT * FROM channel_metrics WHERE channel_key = ? AND snapshot_date >= ? "
                "ORDER BY snapshot_date, id",
                (channel_key, since),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM channel_metrics WHERE channel_key = ? ORDER BY snapshot_date, id",
                (channel_key,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def subs_gained(channel_key: str, since: str) -> int:
    """Tổng subscribers_gained của 1 kênh kể từ `since` (cho retro sub growth)."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(subscribers_gained), 0) AS total FROM channel_metrics "
            "WHERE channel_key = ? AND snapshot_date >= ?",
            (channel_key, since),
        ).fetchone()
        return int(row["total"])
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("channel_metrics module ok")
