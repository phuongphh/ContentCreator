from __future__ import annotations

"""
Storage helper cho bảng `scheduled_posts` — hàng đợi upload theo cadence
(Phase 5 — Distribution). Bảng được tạo ở migration 006_distribution; chạy
`python -m storage.migrate up` trước khi dùng module này.

Vòng đời 1 post: queued → uploading → done | failed. Chuyển queued→uploading
qua `claim()` (UPDATE có điều kiện, atomic) — 2 tick scheduler chạy chồng
nhau không thể cùng upload 1 post. `platform_video_id` được lưu NGAY khi
platform trả về id (mark_done) để một lần restart giữa chừng không bao giờ
tạo video trùng trên YouTube (rủi ro "Upload trùng", phase-5-detailed.md §5).
"""

import logging
import sqlite3
from datetime import datetime
from typing import Optional

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from storage.database import get_connection

logger = logging.getLogger(__name__)

# Trạng thái "đang hoạt động" — chiếm slot và chặn xếp lịch trùng video/kênh.
ACTIVE_STATUSES = ("queued", "uploading", "done")


def _now_str() -> str:
    return datetime.now().isoformat(sep=" ", timespec="seconds")


def insert_post(video_id: int, channel_key: str, scheduled_at: str) -> int:
    """Queue một video cho một kênh tại một thời điểm. Returns post id.

    Raises:
        sqlite3.IntegrityError: nếu slot (channel_key, scheduled_at) đã có
            post đang hoạt động, hoặc video này đã được xếp cho kênh này
            (unique index từ migration 006). Scheduler bắt lỗi này để dò
            slot kế tiếp.
    """
    conn = get_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO scheduled_posts (video_id, channel_key, scheduled_at, status, updated_at) "
            "VALUES (?, ?, ?, 'queued', ?)",
            (video_id, channel_key, scheduled_at, _now_str()),
        )
        conn.commit()
        logger.info("Queued post id=%d video=%d channel=%s at %s",
                    cursor.lastrowid, video_id, channel_key, scheduled_at)
        return cursor.lastrowid
    finally:
        conn.close()


def get_post(post_id: int) -> Optional[dict]:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM scheduled_posts WHERE id = ?", (post_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_due(now: Optional[str] = None, limit: int = 10) -> list[dict]:
    """Post status='queued' đã tới giờ đăng (scheduled_at <= now)."""
    now = now or _now_str()
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM scheduled_posts WHERE status = 'queued' AND scheduled_at <= ? "
            "ORDER BY scheduled_at, id LIMIT ?",
            (now, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def claim(post_id: int) -> bool:
    """Atomically chuyển queued → uploading. True nếu CHÍNH call này chuyển được.

    Cùng cơ chế với database.claim_video_status: chỉ caller thắng race mới
    tiếp tục upload, không bao giờ 2 tick cùng upload 1 post.
    """
    conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE scheduled_posts SET status = 'uploading', updated_at = ? "
            "WHERE id = ? AND status = 'queued'",
            (_now_str(), post_id),
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def mark_done(post_id: int, platform_video_id: Optional[str] = None,
              url: Optional[str] = None) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE scheduled_posts SET status = 'done', platform_video_id = ?, "
            "url = ?, error = NULL, updated_at = ? WHERE id = ?",
            (platform_video_id, url, _now_str(), post_id),
        )
        conn.commit()
    finally:
        conn.close()


def mark_failed(post_id: int, error: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE scheduled_posts SET status = 'failed', error = ?, updated_at = ? "
            "WHERE id = ?",
            (error[:1000], _now_str(), post_id),
        )
        conn.commit()
    finally:
        conn.close()


def slot_taken(channel_key: str, scheduled_at: str) -> bool:
    """True nếu slot này đã có post queued/uploading (done không chặn slot cũ)."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM scheduled_posts WHERE channel_key = ? AND scheduled_at = ? "
            "AND status IN ('queued', 'uploading')",
            (channel_key, scheduled_at),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def find_active(video_id: int, channel_key: str) -> Optional[dict]:
    """Post đang hoạt động (queued/uploading/done) của video này cho kênh này.

    Dùng để idempotent hoá approve: bấm ✅ hai lần không tạo 2 post.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM scheduled_posts WHERE video_id = ? AND channel_key = ? "
            f"AND status IN ({','.join('?' * len(ACTIVE_STATUSES))}) "
            "ORDER BY id DESC LIMIT 1",
            (video_id, channel_key, *ACTIVE_STATUSES),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_by_status(status: str, limit: int = 50) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM scheduled_posts WHERE status = ? "
            "ORDER BY scheduled_at, id LIMIT ?",
            (status, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_stale_uploading(older_than_minutes: int = 90,
                        now: Optional[str] = None) -> list[dict]:
    """Post kẹt ở 'uploading' quá lâu (crash giữa upload).

    KHÔNG tự retry — video có thể ĐÃ lên YouTube trước khi crash (upload xong
    nhưng chưa kịp mark_done); tự đăng lại là tạo video trùng. Scheduler chỉ
    alert để người kiểm tra kênh rồi quyết định mark done/failed thủ công.
    """
    now_dt = datetime.fromisoformat(now) if now else datetime.now()
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM scheduled_posts WHERE status = 'uploading'"
        ).fetchall()
        stale = []
        for r in rows:
            d = dict(r)
            try:
                updated = datetime.fromisoformat(d.get("updated_at") or d["created_at"])
            except (TypeError, ValueError):
                stale.append(d)
                continue
            if (now_dt - updated).total_seconds() > older_than_minutes * 60:
                stale.append(d)
        return stale
    finally:
        conn.close()


def count_by_status() -> dict[str, int]:
    """{'queued': n, 'done': n, ...} — cho health endpoint / báo cáo."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM scheduled_posts GROUP BY status"
        ).fetchall()
        return {r["status"]: r["n"] for r in rows}
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Scheduled posts by status:", count_by_status())
