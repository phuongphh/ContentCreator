from __future__ import annotations

"""
Storage helper cho bảng `video_metrics` (Phase 6 — Analytics & Iteration).

Mỗi row là 1 snapshot số liệu của 1 video trên 1 platform tại 1 ngày. Khoá
upsert là (platform, external_id, snapshot_date): pull lại trong ngày sẽ GHI ĐÈ
snapshot cũ thay vì đẻ thêm dòng (metric có độ trễ 24-48h nên chạy lại cùng
ngày là bình thường — xem phase-6-detailed.md §5 "YouTube Analytics latency").

Yêu cầu migration 007_analytics (`python -m storage.migrate up`).
"""

import logging
from datetime import datetime
from typing import Optional

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from storage.database import get_connection

logger = logging.getLogger(__name__)

# Cột số liệu được phép upsert — allowlist để không dựng UPDATE động từ tên cột
# tuỳ ý (cùng pattern storage/stories.update_status, database.update_video_metadata).
_METRIC_FIELDS = (
    "views", "likes", "comments", "shares", "watch_time_minutes",
    "avg_view_duration_seconds", "retention_50_pct", "ctr",
)


def _today() -> str:
    return datetime.now().date().isoformat()


def resolve_video_id(platform: str, external_id: str) -> Optional[int]:
    """Map một platform video id về `videos.id` qua scheduled_posts.

    Scheduler lưu `platform_video_id` khi upload thành công (Phase 5). Metric
    puller dùng hàm này để nối số liệu về đúng video nội bộ. Trả None nếu
    không map được (vd TikTok upload tay không đi qua scheduler) — caller vẫn
    lưu metric với video_id=NULL.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT video_id FROM scheduled_posts WHERE platform_video_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (external_id,),
        ).fetchone()
        return int(row["video_id"]) if row else None
    except Exception as e:  # bảng chưa tồn tại trên DB pre-migration
        logger.debug("resolve_video_id failed: %s", e)
        return None
    finally:
        conn.close()


def upsert_metric(platform: str, external_id: str, snapshot_date: Optional[str] = None,
                  video_id: Optional[int] = None, channel_key: Optional[str] = None,
                  **metrics) -> int:
    """Ghi (hoặc ghi đè) 1 snapshot metric. Returns row id.

    `metrics`: chỉ nhận các key trong `_METRIC_FIELDS` (ValueError nếu sai).
    Nếu `video_id` không truyền, tự thử map qua resolve_video_id().
    Idempotent theo (platform, external_id, snapshot_date).
    """
    unknown = set(metrics) - set(_METRIC_FIELDS)
    if unknown:
        raise ValueError(f"upsert_metric: unknown metric field(s) {sorted(unknown)}")

    snapshot_date = snapshot_date or _today()
    if video_id is None:
        video_id = resolve_video_id(platform, external_id)

    cols = ["platform", "external_id", "snapshot_date", "video_id", "channel_key"]
    vals = [platform, external_id, snapshot_date, video_id, channel_key]
    for field in _METRIC_FIELDS:
        if field in metrics:
            cols.append(field)
            vals.append(metrics[field])

    placeholders = ", ".join("?" * len(cols))
    # ON CONFLICT trên khoá upsert: cập nhật mọi cột vừa cung cấp (giữ nguyên
    # cột không truyền trong lần pull này — vd YouTube trả ctr, TikTok không).
    update_cols = [c for c in cols if c not in ("platform", "external_id", "snapshot_date")]
    update_clause = ", ".join(f"{c} = excluded.{c}" for c in update_cols)

    conn = get_connection()
    try:
        cur = conn.execute(
            f"INSERT INTO video_metrics ({', '.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(platform, external_id, snapshot_date) DO UPDATE SET {update_clause}",
            vals,
        )
        conn.commit()
        # lastrowid không đáng tin sau ON CONFLICT UPDATE — tra lại id thật.
        row = conn.execute(
            "SELECT id FROM video_metrics WHERE platform = ? AND external_id = ? "
            "AND snapshot_date = ?",
            (platform, external_id, snapshot_date),
        ).fetchone()
        return int(row["id"]) if row else cur.lastrowid
    finally:
        conn.close()


def get_metrics_for_video(video_id: int) -> list[dict]:
    """Mọi snapshot của 1 video (mọi platform), cũ → mới."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM video_metrics WHERE video_id = ? ORDER BY snapshot_date, id",
            (video_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def latest_per_video(platform: Optional[str] = None,
                     since: Optional[str] = None) -> list[dict]:
    """Snapshot MỚI NHẤT của mỗi video (mỗi platform).

    `platform`: lọc theo platform nếu truyền. `since`: chỉ tính snapshot có
    snapshot_date >= since (YYYY-MM-DD) — dùng cho báo cáo tuần.
    """
    where = []
    params: list = []
    if platform:
        where.append("platform = ?")
        params.append(platform)
    if since:
        where.append("snapshot_date >= ?")
        params.append(since)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    conn = get_connection()
    try:
        # Với mỗi (platform, external_id) lấy dòng snapshot_date lớn nhất.
        rows = conn.execute(
            f"""
            SELECT vm.* FROM video_metrics vm
            JOIN (
                SELECT platform, external_id, MAX(snapshot_date) AS md
                FROM video_metrics {where_sql}
                GROUP BY platform, external_id
            ) last
            ON vm.platform = last.platform AND vm.external_id = last.external_id
               AND vm.snapshot_date = last.md
            """,
            params,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def top_videos(metric: str = "views", limit: int = 10,
               since: Optional[str] = None, ascending: bool = False) -> list[dict]:
    """Top/bottom video theo 1 metric, dựa trên snapshot mới nhất mỗi video.

    `ascending=True` → bottom N (dùng cho retro "video cần phân tích"). Bỏ qua
    video có metric NULL để không xếp hạng nhầm chỗ thiếu dữ liệu.
    """
    if metric not in _METRIC_FIELDS:
        raise ValueError(f"top_videos: unknown metric {metric!r}")
    rows = [r for r in latest_per_video(since=since) if r.get(metric) is not None]
    rows.sort(key=lambda r: r[metric], reverse=not ascending)
    return rows[:limit]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    latest = latest_per_video()
    print(f"{len(latest)} video có snapshot mới nhất.")
    for r in top_videos(limit=5):
        print(f"  {r['platform']}:{r['external_id']} views={r.get('views')}")
