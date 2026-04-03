from __future__ import annotations

"""
Scheduler — Xác định loại video cần tạo và platform đăng theo ngày trong tuần.

Lịch đăng (Issue #19 — Dual Content Format):
- Thứ 2, 4, 6 (Mon, Wed, Fri): Video NGẮN (60-90s) → YouTube Shorts + TikTok
- Thứ 3, 5, 7 (Tue, Thu, Sat): Video DÀI (5-10 phút) → YouTube
- Chủ nhật: Nghỉ
"""

import logging
from datetime import date, timedelta

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logger = logging.getLogger(__name__)

# day_of_week: 0=Mon, 1=Tue, ..., 6=Sun
SCHEDULE = {
    0: {"video_type": "short", "platforms": ["youtube_shorts", "tiktok"]},  # Monday
    1: {"video_type": "long",  "platforms": ["youtube"]},                   # Tuesday
    2: {"video_type": "short", "platforms": ["youtube_shorts", "tiktok"]},  # Wednesday
    3: {"video_type": "long",  "platforms": ["youtube"]},                   # Thursday
    4: {"video_type": "short", "platforms": ["youtube_shorts", "tiktok"]},  # Friday
    5: {"video_type": "long",  "platforms": ["youtube"]},                   # Saturday
    # 6 = Sunday: off
}


def get_today_schedule(target_date: date = None) -> dict | None:
    """Get the publishing schedule for today (or a given date).

    Returns:
        dict with keys: video_type, platforms, scheduled_date
        or None if today is a day off.
    """
    if target_date is None:
        target_date = date.today()

    dow = target_date.weekday()
    entry = SCHEDULE.get(dow)

    if entry is None:
        logger.info("No schedule for %s (day off)", target_date)
        return None

    result = {
        "video_type": entry["video_type"],
        "platforms": entry["platforms"],
        "scheduled_date": target_date.isoformat(),
    }
    logger.info("Schedule for %s: %s video → %s",
                target_date, result["video_type"], ", ".join(result["platforms"]))
    return result


def get_next_scheduled_date(from_date: date = None) -> tuple[date, dict]:
    """Find the next scheduled publishing date from a given date.

    Returns:
        Tuple of (date, schedule_entry).
    """
    if from_date is None:
        from_date = date.today()

    for offset in range(1, 8):
        check_date = from_date + timedelta(days=offset)
        schedule = get_today_schedule(check_date)
        if schedule:
            return check_date, schedule

    return from_date + timedelta(days=1), get_today_schedule(from_date + timedelta(days=1))


def get_platform_label(platform: str) -> str:
    """Human-readable platform name."""
    return {
        "youtube": "YouTube",
        "youtube_shorts": "YouTube Shorts",
        "tiktok": "TikTok",
    }.get(platform, platform)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    today = date.today()
    for i in range(7):
        d = today + timedelta(days=i)
        schedule = get_today_schedule(d)
        day_name = d.strftime("%A")
        if schedule:
            print(f"{d} ({day_name}): {schedule['video_type']} → {schedule['platforms']}")
        else:
            print(f"{d} ({day_name}): OFF")
