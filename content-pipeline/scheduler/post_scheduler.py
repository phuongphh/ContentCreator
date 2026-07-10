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
from channels import get_channel
from storage import scheduled_posts
from storage.database import get_video, update_video_status, update_video_publish_url

logger = logging.getLogger(__name__)

# (channel_key, track, video_type) → danh sách slot spec.
# Lịch phát sóng thống nhất cả 2 kênh: short đăng Thứ 2–7, long đăng Chủ nhật
# (khớp publisher/scheduler.py — lịch sản xuất). TikTok KHÔNG có mặt ở đây: từ
# yêu cầu mới, TikTok đi theo mô hình gửi video qua Telegram (kênh Bé MC) cho
# user tự upload — không auto-schedule/auto-upload (xem review_bot._route_to_channel).
CADENCE: dict[tuple[str, str, str], list[str]] = {
    ("ai_youtube", "ai", "short"):       ["mon-sat 12:00"],
    ("ai_youtube", "ai", "long"):        ["sun 20:00"],
    ("drama_youtube", "drama", "short"): ["mon-sat 12:00"],
    ("drama_youtube", "drama", "long"):  ["sun 20:00"],
}
# Combo ngoài CADENCE vẫn được xếp lịch thay vì rơi rụng im lặng.
DEFAULT_SLOTS = ["12:00"]

_WEEKDAYS = {
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4,
    "saturday": 5, "sunday": 6,
}


def _parse_weekday_token(token: str) -> frozenset[int]:
    """'sun' → {6}; 'mon-sat' → {0..5}; 'mon,wed,fri' → {0,2,4}.

    Hỗ trợ range (dấu '-', gói vòng cuối tuần nếu cần) và list (dấu ',').
    Raises ValueError cho tên thứ sai.
    """
    days: set[int] = set()
    for part in token.split(","):
        part = part.strip()
        if "-" in part:
            lo_s, _, hi_s = part.partition("-")
            if lo_s not in _WEEKDAYS or hi_s not in _WEEKDAYS:
                raise ValueError(f"Unknown weekday in range: {part!r}")
            lo, hi = _WEEKDAYS[lo_s], _WEEKDAYS[hi_s]
            # Wrap quanh tuần (vd 'sat-mon' = {5,6,0}); span 7 ngày.
            days.update((lo + i) % 7 for i in range((hi - lo) % 7 + 1))
        else:
            if part not in _WEEKDAYS:
                raise ValueError(f"Unknown weekday: {part!r}")
            days.add(_WEEKDAYS[part])
    return frozenset(days)


def _parse_slot_spec(spec: str) -> tuple[frozenset[int] | None, int, int]:
    """'21:00' → (None, 21, 0); 'sun 20:00' → ({6}, 20, 0);
    'mon-sat 12:00' → ({0,1,2,3,4,5}, 12, 0).

    weekdays None = mọi ngày. Raises ValueError cho spec sai (bắt ngay lúc dev).
    """
    parts = spec.strip().lower().split()
    if len(parts) == 1:
        weekdays = None
        time_part = parts[0]
    elif len(parts) == 2:
        weekdays = _parse_weekday_token(parts[0])
        time_part = parts[1]
    else:
        raise ValueError(f"Bad slot spec: {spec!r}")
    hour_str, _, minute_str = time_part.partition(":")
    hour, minute = int(hour_str), int(minute_str or 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Bad time in slot spec: {spec!r}")
    return weekdays, hour, minute


def iter_slots(specs: list[str], after: datetime, days: int = 8) -> list[datetime]:
    """Mọi slot > `after` trong `days` ngày tới, sort tăng dần."""
    slots = []
    for spec in specs:
        weekdays, hour, minute = _parse_slot_spec(spec)
        for offset in range(days + 1):
            day = after.date() + timedelta(days=offset)
            if weekdays is not None and day.weekday() not in weekdays:
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
        details = []
        for p in stale:
            line = (f"  • post {p['id']}: video {p['video_id']} → "
                    f"{p['channel_key']} lúc {p['scheduled_at']}")
            # platform_video_id đã có = video ĐÃ live, chỉ thiếu mark done.
            if p.get("platform_video_id"):
                line += (f"\n    → ĐÃ lên platform ({p['url'] or p['platform_video_id']}), "
                         f"chỉ cần mark done tay")
            else:
                line += "\n    → chưa rõ đã lên chưa — kiểm tra kênh trước"
            details.append(line)
        _alert_safe(
            "⚠️ %d post kẹt ở trạng thái 'uploading' (crash giữa upload?):\n%s\n"
            "Không tự retry để tránh upload trùng." % (len(stale), "\n".join(details))
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
        # on_uploaded ghi platform_video_id vào post NGAY khi YouTube trả về
        # id (giữ status 'uploading') — crash trong bước thumbnail/caption
        # sau đó vẫn để lại bằng chứng video đã live, alert stale bên dưới
        # nhờ vậy phân biệt được "đã lên sóng" với "chưa biết".
        result = upload_to_youtube(
            post["video_id"], post["channel_key"],
            on_uploaded=lambda vid, url: scheduled_posts.record_platform_id(
                post["id"], vid, url),
        )
        if not result:
            return False, "upload_to_youtube failed (see log)", None
        return True, result["url"], result["youtube_video_id"]

    if channel["platform"] == "tiktok":
        # Mô hình TikTok mới: KHÔNG auto-upload. Gửi video qua Telegram (kênh
        # Bé MC) để upload tay. Nhánh này chỉ chạy nếu còn post tiktok cũ trong
        # queue (routing mới không tạo post tiktok nữa) — giữ để nhất quán.
        from notifier.telegram_bot import send_tiktok_manual
        if send_tiktok_manual(post["video_id"]):
            return True, "telegram://be_mc", None
        return False, "gửi video tới Bé MC thất bại (xem log)", None

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
