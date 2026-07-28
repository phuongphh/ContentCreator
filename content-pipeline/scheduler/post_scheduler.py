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
    """Upload mọi post tới giờ.

    Returns {'uploaded': n, 'failed': n, 'stale': n, 'retried': n} — `retried` là
    số post chết vì token OAuth và đã được xếp lại lịch (issue #109), KHÔNG tính
    vào `failed` vì video vẫn còn cơ hội lên sóng.
    """
    now = now or datetime.now()
    now_str = now.isoformat(sep=" ", timespec="seconds")
    summary = {"uploaded": 0, "failed": 0, "stale": 0, "retried": 0}

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
        auth_error = False
        try:
            result = _dispatch(post)
        except Exception as e:  # không để 1 post hỏng chặn các post còn lại
            logger.exception("Dispatch error for post %d", post["id"])
            result = (False, f"{type(e).__name__}: {e}", None)
            auth_error = _is_auth_error(e)

        ok, url_or_error, platform_video_id = result
        # `now` của tick (không phải wall clock) để giờ retry luôn nhất quán với
        # khung thời gian mà run_tick đang chạy.
        if not ok and auth_error and _retry_after_auth_error(post, url_or_error, now):
            summary["retried"] += 1
            continue
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
            msg = (f"❌ Upload thất bại: video {post['video_id']} → "
                   f"{post['channel_key']} (post {post['id']}):\n{url_or_error}")
            if auth_error:
                # Hết lượt retry mà token vẫn chết → nhắc lại đúng cách cấp lại
                # và cách đẩy video đi sau khi cấp (issue #109).
                msg += (f"\n\nĐã thử lại {config.POST_AUTH_RETRY_MAX} lần, token "
                        f"vẫn chưa được cấp lại.\n"
                        f"{_reauth_hint(post['channel_key'])}\n"
                        f"Cấp lại xong, đẩy video đi bằng: python -m "
                        f"scheduler.post_scheduler requeue {post['id']}")
            _alert_safe(msg)
            summary["failed"] += 1

    if any(summary.values()):
        logger.info("Scheduler tick: %s", summary)
    return summary


def requeue_post(post_id: int, in_minutes: int = 1,
                 now: datetime | None = None) -> str:
    """Đẩy lại 1 post đã failed — phục hồi TAY sau khi cấp lại token (#109).

    An toàn kép: `scheduled_posts.requeue` từ chối post đã có `platform_video_id`
    (đã lên sóng) và post không ở trạng thái uploading/failed, nên lệnh này
    không thể tạo video trùng. `attempts` được reset về 0 vì đây là hành động có
    chủ đích của người vận hành, không phải retry tự động.
    """
    post = scheduled_posts.get_post(post_id)
    if not post:
        return f"Không có post {post_id}."
    if post.get("platform_video_id"):
        return (f"Post {post_id} ĐÃ lên platform "
                f"({post.get('url') or post['platform_video_id']}) — không đẩy "
                f"lại (tránh video trùng). Nếu cần, mark done tay.")

    now = now or datetime.now()
    base = now + timedelta(minutes=max(in_minutes, 0))
    for minute in range(10):
        slot = (base + timedelta(minutes=minute)).isoformat(sep=" ", timespec="seconds")
        try:
            if scheduled_posts.requeue(post_id, slot, error=None, reset_attempts=True):
                return (f"Post {post_id} (video {post['video_id']} → "
                        f"{post['channel_key']}) đã xếp lại lúc {slot}. "
                        f"Tick kế tiếp sẽ upload.")
            return (f"Post {post_id} đang ở trạng thái {post['status']!r} — chỉ "
                    f"đẩy lại được post 'failed'/'uploading'.")
        except sqlite3.IntegrityError:
            continue
        except sqlite3.OperationalError as e:
            return (f"Thiếu schema mới ({e}) — chạy `python -m storage.migrate up` "
                    f"rồi thử lại.")
    return f"Không tìm được slot trống quanh {base:%H:%M} cho post {post_id}."


def _is_auth_error(exc: BaseException) -> bool:
    """Lỗi TOKEN OAuth (invalid_grant) chứ không phải lỗi mạng/upload — #109.

    Uỷ cho publisher.token_health (module sở hữu mọi hiểu biết về token) để chỉ
    có MỘT định nghĩa "lỗi token" trong codebase. Thiếu module/dependency thì
    coi như không phải lỗi auth → hành vi cũ (mark_failed), không bao giờ retry
    nhầm.
    """
    try:
        from publisher.token_health import is_auth_error
        return is_auth_error(exc)
    except Exception as e:
        logger.warning("Không phân loại được lỗi dispatch (%s) — coi như không "
                       "phải lỗi token", e)
        return False


def _retry_after_auth_error(post: dict, error: str | None,
                            now: datetime | None = None) -> bool:
    """Requeue post chết vì token, có giới hạn. True nếu đã xếp lại lịch.

    Vì sao retry ở đây là AN TOÀN dù nguyên tắc chung của module là "không bao
    giờ tự retry": RefreshError xảy ra trong `_get_authenticated_service`, tức
    TRƯỚC khi `videos.insert` gửi byte đầu tiên — không thể có video trùng trên
    kênh. Post đã kịp có `platform_video_id` bị `scheduled_posts.requeue` từ
    chối như chốt chặn cuối, độc lập với phán đoán ở đây.

    Vì sao đáng làm: cấp lại token là thao tác TAY mất hàng giờ (issue #109 —
    token chết 12:00, người dùng thấy alert lúc nào hay lúc đó). Không requeue
    thì video mồ côi vĩnh viễn dù đã cấp lại token; requeue mỗi tick 5 phút thì
    nã alert. Nên: lùi POST_AUTH_RETRY_DELAY_MINUTES phút, tối đa
    POST_AUTH_RETRY_MAX lần, alert lần ĐẦU và lần CUỐI.
    """
    now = now or datetime.now()
    attempts = post.get("attempts") or 0
    channel_key = post["channel_key"]

    if attempts >= config.POST_AUTH_RETRY_MAX:
        return False  # caller mark_failed + alert như cũ

    # Dò phút trống kế tiếp: unique index (channel_key, scheduled_at) chặn 2 post
    # cùng slot, mà giờ retry có thể trùng slot cadence của post khác.
    base = now + timedelta(minutes=config.POST_AUTH_RETRY_DELAY_MINUTES)
    try:
        for minute in range(10):
            slot = (base + timedelta(minutes=minute)).isoformat(sep=" ",
                                                                timespec="seconds")
            try:
                if scheduled_posts.requeue(post["id"], slot, error=error):
                    break
                return False  # post không còn ở trạng thái requeue được (đã done?)
            except sqlite3.IntegrityError:
                continue
        else:
            logger.warning("Không tìm được slot retry cho post %d", post["id"])
            return False
    except sqlite3.OperationalError as e:
        # Điển hình: quên `python -m storage.migrate up` sau khi pull (cột
        # `attempts` của migration 009 chưa có). Suy giảm êm về hành vi cũ
        # (mark_failed + alert) thay vì làm sập cả tick và chặn các post khác.
        logger.error("Không requeue được post %d (%s) — chạy `python -m "
                     "storage.migrate up`?", post["id"], e)
        return False

    attempt_no = attempts + 1
    logger.warning("Post %d (%s) chết vì token — retry lần %d/%d lúc %s",
                   post["id"], channel_key, attempt_no,
                   config.POST_AUTH_RETRY_MAX, slot)
    if attempt_no == 1:
        # Chỉ alert lần đầu: các lần sau là cùng một sự cố, im lặng cho tới khi
        # hết lượt (alert cuối do nhánh mark_failed của run_tick gửi).
        _alert_safe(_auth_alert(channel_key, post, error, slot, attempt_no))
    return True


def _reauth_hint(channel_key: str) -> str:
    """Câu lệnh cấp lại token của kênh (token_health là nguồn duy nhất)."""
    try:
        from publisher.token_health import reauth_command
        from publisher.youtube_uploader import resolve_token_file
        return reauth_command(resolve_token_file(channel_key))
    except Exception:
        return "Cấp lại token: xem docs/current/oauth-setup.md §1.5"


def _auth_alert(channel_key: str, post: dict, error: str | None, slot: str,
                attempt_no: int) -> str:
    """Alert token chết lúc upload — dùng chung câu lệnh cấp lại với token_health."""
    extra = (f"Video {post['video_id']} (post {post['id']}) KHÔNG mất: đã xếp lại "
             f"lúc {slot}, tự thử lại tối đa {config.POST_AUTH_RETRY_MAX} lần "
             f"(lần {attempt_no}). Cấp lại token xong là video tự lên sóng.")
    try:
        from publisher.token_health import auth_failure_alert
        return auth_failure_alert(channel_key, error or "invalid_grant", extra)
    except Exception:  # token_health không import được → alert tối giản
        return (f"🔴 Token YouTube kênh {channel_key} chết — upload thất bại.\n"
                f"{error}\n{extra}")


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
    p_requeue = sub.add_parser(
        "requeue",
        help="Đẩy lại 1 post đã failed (vd sau khi cấp lại token — issue #109)")
    p_requeue.add_argument("post_id", type=int)
    p_requeue.add_argument("--in-minutes", type=int, default=1,
                           help="Bao nhiêu phút nữa thì đăng (mặc định 1)")
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
    elif args.command == "requeue":
        print(requeue_post(args.post_id, in_minutes=args.in_minutes))


if __name__ == "__main__":
    main()
