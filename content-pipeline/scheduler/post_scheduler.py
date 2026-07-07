from __future__ import annotations

"""
Post Scheduler (Phase 5 EPIC #5.4) — queue video đã duyệt vào slot đăng theo
cadence chuẩn, và tick mỗi 5 phút để upload các post tới giờ.

Khác phase-5-detailed.md một chi tiết: doc dùng key phẳng kiểu
"drama_youtube_shorts"/"tiktok_drama" — không tồn tại trong channel registry
(channels.py chỉ có ai_youtube/drama_youtube/tiktok_main, TikTok là 1 account
mixed cho cả 2 track). CADENCE ở đây key theo (channel_key, track, video_type)
để vẫn diễn đạt đủ 6 dòng cadence của doc mà không phải bịa thêm channel key
ngoài registry.

Chạy:
    python -m scheduler.post_scheduler tick       # launchd mỗi 5 phút
    python -m scheduler.post_scheduler list       # xem queue
    python -m scheduler.post_scheduler schedule <video_id> <channel_key>

Chống upload trùng (resume-from-crash, phase-5-detailed.md §5):
- tick chỉ nhặt post status='queued' và claim atomic sang 'uploading' trước
  khi upload — 2 tick chạy chồng nhau không thể cùng upload 1 post.
- post kẹt ở 'uploading' (crash giữa upload) KHÔNG bị tự retry — video có thể
  đã lên platform trước khi crash; tick chỉ alert Telegram để người kiểm tra.
"""

import argparse
import logging
import sqlite3
from datetime import datetime, timedelta

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
from channels import get_channel
from storage import scheduled_posts
from storage.database import get_video, update_video_status, update_video_publish_url

logger = logging.getLogger(__name__)

# (channel_key, track, video_type) → danh sách slot spec.
# Slot spec: "HH:MM" (hàng ngày) hoặc "<thứ> HH:MM" (hàng tuần, mon..sun).
CADENCE: dict[tuple[str, str, str], list[str]] = {
    ("drama_youtube", "drama", "short"): ["12:00", "21:00"],
    ("drama_youtube", "drama", "long"):  ["sun 20:00"],
    ("ai_youtube", "ai", "short"):       ["12:00"],
    ("ai_youtube", "ai", "long"):        ["tue 19:00", "sat 19:00"],
    ("tiktok_main", "drama", "short"):   ["12:00", "21:00"],
    ("tiktok_main", "ai", "short"):      ["19:00"],
}
# Combo ngoài CADENCE (vd video long cho tiktok — không nên xảy ra) vẫn được
# xếp lịch thay vì rơi rụng im lặng.
DEFAULT_SLOTS = ["12:00"]

_WEEKDAYS = {
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4,
    "saturday": 5, "sunday": 6,
}


def _parse_slot_spec(spec: str) -> tuple[int | None, int, int]:
    """'21:00' → (None, 21, 0); 'sun 20:00' → (6, 20, 0).

    Raises ValueError cho spec sai định dạng (bắt ngay lúc dev, không đợi runtime).
    """
    parts = spec.strip().lower().split()
    if len(parts) == 1:
        weekday = None
        time_part = parts[0]
    elif len(parts) == 2:
        if parts[0] not in _WEEKDAYS:
            raise ValueError(f"Unknown weekday in slot spec: {spec!r}")
        weekday = _WEEKDAYS[parts[0]]
        time_part = parts[1]
    else:
        raise ValueError(f"Bad slot spec: {spec!r}")
    hour_str, _, minute_str = time_part.partition(":")
    hour, minute = int(hour_str), int(minute_str or 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Bad time in slot spec: {spec!r}")
    return weekday, hour, minute


def iter_slots(specs: list[str], after: datetime, days: int = 8) -> list[datetime]:
    """Mọi slot > `after` trong `days` ngày tới, sort tăng dần."""
    slots = []
    for spec in specs:
        weekday, hour, minute = _parse_slot_spec(spec)
        for offset in range(days + 1):
            day = after.date() + timedelta(days=offset)
            if weekday is not None and day.weekday() != weekday:
                continue
            candidate = datetime.combine(
                day, datetime.min.time()).replace(hour=hour, minute=minute)
            if candidate > after:
                slots.append(candidate)
    return sorted(slots)


def slots_for_video(video: dict, channel_key: str) -> list[str]:
    """Tra CADENCE cho (kênh, track, loại video); combo lạ → DEFAULT_SLOTS."""
    track = video.get("track") or get_channel(channel_key)["track"]
    video_type = video.get("video_type") or "short"
    key = (channel_key, track, video_type)
    specs = CADENCE.get(key)
    if specs is None:
        logger.warning("No cadence for %s — using default %s", key, DEFAULT_SLOTS)
        return DEFAULT_SLOTS
    return specs


def schedule_video(video_id: int, channel_key: str,
                   now: datetime | None = None) -> dict | None:
    """Queue video vào slot trống kế tiếp theo CADENCE. Returns post row.

    Idempotent: video đã có post đang hoạt động (queued/uploading/done) cho
    kênh này thì trả lại post đó thay vì xếp thêm — bấm ✅ approve 2 lần
    không tạo 2 lịch đăng.
    """
    get_channel(channel_key)  # raise ValueError sớm nếu key sai
    video = get_video(video_id)
    if not video:
        logger.error("schedule_video: video %d not found", video_id)
        return None

    existing = scheduled_posts.find_active(video_id, channel_key)
    if existing:
        logger.info("Video %d already scheduled for %s (post %d, %s)",
                    video_id, channel_key, existing["id"], existing["status"])
        return existing

    now = now or datetime.now()
    specs = slots_for_video(video, channel_key)
    # Slot dày nhất là 1 slot/ngày/dòng cadence — 30 ngày dò là quá đủ; hết 30
    # ngày mà vẫn kẹt nghĩa là có bug/backlog bất thường, nên báo lỗi thay vì
    # âm thầm xếp lịch sang tháng sau.
    for candidate in iter_slots(specs, now, days=30):
        slot_str = candidate.isoformat(sep=" ", timespec="seconds")
        if scheduled_posts.slot_taken(channel_key, slot_str):
            continue
        try:
            post_id = scheduled_posts.insert_post(video_id, channel_key, slot_str)
        except sqlite3.IntegrityError:
            # Thua race giành slot (unique index) — thử slot kế tiếp; nhưng
            # nếu là unique (video, channel) thì post active vừa được tạo ở
            # nơi khác → trả về post đó (idempotent).
            existing = scheduled_posts.find_active(video_id, channel_key)
            if existing:
                return existing
            continue
        return scheduled_posts.get_post(post_id)

    logger.error("No free slot within 30 days for video %d → %s", video_id, channel_key)
    return None


def run_tick(now: datetime | None = None) -> dict:
    """Upload mọi post tới giờ. Returns {'uploaded': n, 'failed': n, 'stale': n}."""
    now = now or datetime.now()
    now_str = now.isoformat(sep=" ", timespec="seconds")
    summary = {"uploaded": 0, "failed": 0, "stale": 0}

    stale = scheduled_posts.get_stale_uploading(now=now_str.replace(" ", "T"))
    if stale:
        summary["stale"] = len(stale)
        _alert_safe(
            "⚠️ %d post kẹt ở trạng thái 'uploading' (crash giữa upload?):\n%s\n"
            "Kiểm tra kênh xem video ĐÃ lên chưa rồi xử lý tay — không tự "
            "retry để tránh upload trùng." % (
                len(stale),
                "\n".join(f"  • post {p['id']}: video {p['video_id']} → "
                          f"{p['channel_key']} lúc {p['scheduled_at']}" for p in stale),
            )
        )

    for post in scheduled_posts.get_due(now=now_str):
        if not scheduled_posts.claim(post["id"]):
            continue  # tick khác vừa nhận post này
        try:
            result = _dispatch(post)
        except Exception as e:  # không để 1 post hỏng chặn các post còn lại
            logger.exception("Dispatch error for post %d", post["id"])
            result = (False, f"{type(e).__name__}: {e}", None)

        ok, url_or_error, platform_video_id = result
        if ok:
            scheduled_posts.mark_done(post["id"], platform_video_id=platform_video_id,
                                      url=url_or_error)
            update_video_status(post["video_id"], "published")
            if url_or_error:
                update_video_publish_url(post["video_id"], url_or_error)
            _notify_published_safe(post, url_or_error)
            summary["uploaded"] += 1
        else:
            scheduled_posts.mark_failed(post["id"], url_or_error or "unknown error")
            _alert_safe(f"❌ Upload thất bại: video {post['video_id']} → "
                        f"{post['channel_key']} (post {post['id']}):\n{url_or_error}")
            summary["failed"] += 1

    if any(summary.values()):
        logger.info("Scheduler tick: %s", summary)
    return summary


def _dispatch(post: dict) -> tuple[bool, str | None, str | None]:
    """Upload 1 post theo platform của kênh. Returns (ok, url|error, platform_id)."""
    channel = get_channel(post["channel_key"])
    video = get_video(post["video_id"])
    if not video:
        return False, f"video {post['video_id']} not found", None

    if channel["platform"] == "youtube":
        from publisher.youtube_uploader import upload_to_youtube
        result = upload_to_youtube(post["video_id"], post["channel_key"])
        if not result:
            return False, "upload_to_youtube failed (see log)", None
        return True, result["url"], result["youtube_video_id"]

    if channel["platform"] == "tiktok":
        if config.TIKTOK_ACCESS_TOKEN:
            from publisher.tiktok_uploader import upload_video
            publish_id = upload_video(
                video.get("video_path", ""),
                caption=video.get("tiktok_caption", ""),
                hashtags=video.get("tiktok_hashtags", ""),
            )
            if not publish_id:
                return False, "TikTok API upload failed (see log)", None
            return True, f"tiktok://publish/{publish_id}", publish_id
        # Chưa có API token (approval 2-4 tuần) → rơi về manual queue thay vì
        # fail: file nằm sẵn trong queue_tiktok/ cho Phuong upload tay.
        from publisher.tiktok_manual import export_for_manual_upload
        exported = export_for_manual_upload(post["video_id"])
        if not exported:
            return False, "manual export failed (see log)", None
        return True, f"file://{exported}", None

    return False, f"unknown platform {channel['platform']!r}", None


def _notify_published_safe(post: dict, url: str | None) -> None:
    try:
        from notifier.telegram_bot import send_publish_notification
        channel = get_channel(post["channel_key"])
        label = f"{channel['name']} ({post['channel_key']})"
        if url and url.startswith("file://"):
            label += " — QUEUE TAY, chưa lên sóng"
        send_publish_notification(post["video_id"], label, url or "")
    except Exception as e:
        logger.warning("Publish notification failed (non-fatal): %s", e)


def _alert_safe(text: str) -> None:
    try:
        from notifier.telegram_bot import send_alert
        send_alert(text)
    except Exception as e:
        logger.warning("Alert failed (non-fatal): %s", e)


def main():
    parser = argparse.ArgumentParser(description="Post scheduler (Phase 5)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("tick", help="Upload mọi post tới giờ (chạy mỗi 5 phút)")
    sub.add_parser("list", help="Xem queue")
    p_sched = sub.add_parser("schedule", help="Xếp lịch 1 video vào slot kế tiếp")
    p_sched.add_argument("video_id", type=int)
    p_sched.add_argument("channel_key")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    if args.command == "tick":
        print(run_tick())
    elif args.command == "list":
        for status in ("queued", "uploading", "failed"):
            for p in scheduled_posts.get_by_status(status):
                print(f"[{p['status']}] post {p['id']}: video {p['video_id']} → "
                      f"{p['channel_key']} lúc {p['scheduled_at']}"
                      + (f" ({p['error']})" if p.get("error") else ""))
    elif args.command == "schedule":
        post = schedule_video(args.video_id, args.channel_key)
        print(post if post else "Không xếp được lịch — xem log.")


if __name__ == "__main__":
    main()
