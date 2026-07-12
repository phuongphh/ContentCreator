from __future__ import annotations

"""
Storage helper cho bảng `stories` — CRUD cho Drama track (Phase 2).

Bảng `stories` được tạo ở migration 001_multi_track; cột `title`/`metadata`
và unique index trên `source_id` được thêm ở migration 002_stories_metadata
(xem storage/migrate.py). Chạy `python -m storage.migrate up` trước khi dùng
module này.
"""

import json
import logging
import sqlite3
from typing import Optional

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from storage.database import get_connection

logger = logging.getLogger(__name__)

# Columns update_status() is allowed to touch besides `status`, to avoid
# building a dynamic UPDATE from caller-controlled column names.
_UPDATABLE_FIELDS = {
    "rubric_score", "rewritten_content", "destination", "produced_at", "title",
}


def insert_story(source: str, source_id: Optional[str], raw_content: str,
                 track: str = "drama", title: Optional[str] = None,
                 metadata: Optional[dict] = None) -> int:
    """Insert a new story with status='pending'. Returns the story id.

    Raises:
        sqlite3.IntegrityError: nếu `source_id` đã tồn tại (unique index từ
            migration 002). Gọi `dedupe_check()` trước nếu muốn tránh raise.
    """
    conn = get_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO stories (source, source_id, raw_content, track, title, metadata, status) "
            "VALUES (?, ?, ?, ?, ?, ?, 'pending')",
            (
                source, source_id, raw_content, track, title,
                json.dumps(metadata, ensure_ascii=False) if metadata is not None else None,
            ),
        )
        conn.commit()
        logger.info("Inserted story id=%d source=%s source_id=%s", cursor.lastrowid, source, source_id)
        return cursor.lastrowid
    finally:
        conn.close()


def dedupe_check(source_id: str) -> bool:
    """True nếu đã có story với `source_id` này."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM stories WHERE source_id = ?", (source_id,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def get_story(story_id: int) -> Optional[dict]:
    """Lấy 1 story theo id."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM stories WHERE id = ?", (story_id,)).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


def get_pending(limit: int = 10, track: Optional[str] = None) -> list[dict]:
    """Lấy story status='pending', sort theo created_at DESC.

    Xem `get_by_status()` — đây chỉ là shortcut cho status='pending' (dùng
    nhiều nhất: seed bot, scorer, rewriter).
    """
    return get_by_status("pending", limit=limit, track=track)


def get_by_status(status: str, limit: int = 10, track: Optional[str] = None) -> list[dict]:
    """Lấy story theo `status` bất kỳ, sort theo created_at DESC.

    Tie-broken bằng `id DESC`: SQLite's CURRENT_TIMESTAMP chỉ có độ chính xác
    tới giây, nên nhiều story insert trong cùng 1 giây (bình thường với 1 lần
    chạy collector) sẽ có `created_at` giống hệt nhau — dùng `id` (tăng dần
    theo thứ tự insert) làm tie-breaker để thứ tự luôn ổn định/đúng insert order.

    Args:
        status: 'pending', 'approved', 'rejected', 'needs_review', 'produced', ...
        track: lọc theo track ('drama', 'ai', ...) nếu truyền vào, mặc định
            lấy mọi track.
    """
    conn = get_connection()
    try:
        if track:
            rows = conn.execute(
                "SELECT * FROM stories WHERE status = ? AND track = ? "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (status, track, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM stories WHERE status = ? "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def count_producible(track: str = "drama") -> int:
    """Số story còn có thể sản xuất (status 'pending' | 'approved') cho 1 track.

    Dùng cho cảnh báo backlog cạn (storage/collector_health.check_drama_backlog,
    issue #78): khi Reddit tắt, track Drama sống bằng seed thủ công, nên tín hiệu
    sức khoẻ đúng là "còn đủ story để sản xuất không", không phải "collector có
    chạy không". 'pending' = chờ chấm điểm/Việt hoá; 'approved' = chờ render.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM stories "
            "WHERE track = ? AND status IN ('pending', 'approved')",
            (track,),
        ).fetchone()
        return row["n"] if row else 0
    finally:
        conn.close()


def update_status(story_id: int, status: str, **fields) -> None:
    """Cập nhật status + các field khác (rubric_score, rewritten_content, ...).

    Raises:
        ValueError: nếu `fields` chứa tên cột không nằm trong allowlist
            (`_UPDATABLE_FIELDS`) — tránh dựng UPDATE động từ tên cột tuỳ ý.
    """
    unknown = set(fields) - _UPDATABLE_FIELDS
    if unknown:
        raise ValueError(f"update_status: unknown field(s) {sorted(unknown)}")

    set_clauses = ["status = ?"]
    params: list = [status]
    for key, value in fields.items():
        set_clauses.append(f"{key} = ?")
        params.append(value)
    params.append(story_id)

    conn = get_connection()
    try:
        conn.execute(
            f"UPDATE stories SET {', '.join(set_clauses)} WHERE id = ?", params
        )
        conn.commit()
    finally:
        conn.close()


def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    if row is None:
        return None
    d = dict(row)
    if d.get("metadata"):
        try:
            d["metadata"] = json.loads(d["metadata"])
        except (json.JSONDecodeError, TypeError):
            pass  # leave as raw string — malformed metadata shouldn't break callers
    return d


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Pending stories:", len(get_pending()))
