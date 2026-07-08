from __future__ import annotations

"""
Quota tracking cho YouTube Data API v3 (Phase 5 EPIC #5.2 — Quota tracking).

Mỗi lần gọi API tốn unit (videos.insert ~1600, thumbnails.set ~50); quota
mặc định 10.000 unit/ngày/Google Cloud Project và reset lúc NỬA ĐÊM GIỜ
PACIFIC — nên ngày quota được tính theo America/Los_Angeles chứ không phải
giờ local, kẻo cảnh báo lệch tới nửa ngày.

`add_units()` trả về flag "vừa vượt ngưỡng cảnh báo" đúng MỘT lần cho mỗi lần
băng qua ngưỡng (before < threshold <= after) — caller (youtube_uploader)
gửi Telegram alert dựa trên flag đó, không cần chống spam alert riêng.

Bảng `quota_usage` tạo ở migration 006_distribution.
"""

import logging
from datetime import datetime, timezone

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
from storage.database import get_connection

logger = logging.getLogger(__name__)

# Chi phí unit các call chính (docs: developers.google.com/youtube/v3/determine_quota_cost)
UNITS_VIDEO_INSERT = 1600
UNITS_THUMBNAIL_SET = 50
UNITS_CAPTION_INSERT = 400


def quota_date(now: datetime | None = None) -> str:
    """Ngày quota hiện tại (YYYY-MM-DD) theo giờ Pacific.

    Nếu tzdata không có (hiếm), fallback về UTC-8 cố định — sai lệch tối đa
    1 giờ quanh DST, chấp nhận được cho mục đích cảnh báo.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        pt = now.astimezone(ZoneInfo("America/Los_Angeles"))
    except Exception:  # tzdata missing
        from datetime import timedelta
        pt = now.astimezone(timezone(timedelta(hours=-8)))
    return pt.date().isoformat()


def units_used_today(service: str = "youtube") -> int:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(units), 0) AS total FROM quota_usage "
            "WHERE service = ? AND date = ?",
            (service, quota_date()),
        ).fetchone()
        return int(row["total"])
    finally:
        conn.close()


def add_units(units: int, service: str = "youtube",
              note: str | None = None) -> tuple[int, bool]:
    """Ghi nhận `units` vừa tiêu. Returns (tổng hôm nay, vừa_vượt_ngưỡng).

    `vừa_vượt_ngưỡng` chỉ True ở đúng lần ghi làm tổng băng qua
    config.YOUTUBE_DAILY_QUOTA × config.QUOTA_ALERT_RATIO.
    """
    date = quota_date()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(units), 0) AS total FROM quota_usage "
            "WHERE service = ? AND date = ?",
            (service, date),
        ).fetchone()
        before = int(row["total"])
        conn.execute(
            "INSERT INTO quota_usage (service, date, units, note) VALUES (?, ?, ?, ?)",
            (service, date, units, note),
        )
        conn.commit()
    finally:
        conn.close()

    after = before + units
    threshold = config.YOUTUBE_DAILY_QUOTA * config.QUOTA_ALERT_RATIO
    crossed = before < threshold <= after
    if crossed:
        logger.warning("%s quota crossed %.0f%%: %d/%d units used today",
                       service, config.QUOTA_ALERT_RATIO * 100, after,
                       config.YOUTUBE_DAILY_QUOTA)
    return after, crossed


def record_youtube_units(units: int, note: str | None = None) -> int:
    """add_units + gửi Telegram alert nếu vừa vượt ngưỡng. Returns tổng hôm nay.

    Alert lỗi (Telegram down) không được làm hỏng upload — nuốt exception.
    """
    total, crossed = add_units(units, service="youtube", note=note)
    if crossed:
        try:
            from notifier.telegram_bot import send_alert
            pct = 100.0 * total / config.YOUTUBE_DAILY_QUOTA
            send_alert(
                f"⚠️ YouTube API quota đã dùng {total}/{config.YOUTUBE_DAILY_QUOTA} "
                f"unit hôm nay ({pct:.0f}%). Mỗi upload ~{UNITS_VIDEO_INSERT} unit — "
                f"còn ~{max(0, (config.YOUTUBE_DAILY_QUOTA - total)) // UNITS_VIDEO_INSERT} lượt upload."
            )
        except Exception as e:
            logger.error("Quota alert failed: %s", e)
    return total


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(f"YouTube units used today ({quota_date()}):", units_used_today())
