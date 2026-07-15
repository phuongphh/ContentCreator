from __future__ import annotations

"""
Review Bot (Phase 5 EPIC #5.1 — Telegram Review Gate).

Push video preview (<50MB, tự nén nếu quá cỡ — video/preview.py) kèm inline
keyboard ✅ Duyệt / ❌ Loại / ✏️ Sửa metadata. Approve → xếp lịch upload qua
scheduler/post_scheduler.py (KHÔNG publish ngay như flow /approve cũ của
track AI); TikTok chưa có API token thì export thẳng vào queue tay.

Khác phase-5-detailed.md: doc vẽ review_bot như một bot mới; thực tế đây là
các HANDLER THUẦN được notifier/telegram_bot.py gọi trong CÙNG vòng
long-polling đang chạy — cùng lý do với seed_bot Phase 2 (Telegram chỉ cho 1
getUpdates connection mỗi bot token; process thứ hai sẽ 409 Conflict liên tục).

State hội thoại (đang chờ nhập lý do reject / giá trị metadata mới) lưu ở
`notifier/.review_state.json`, sống qua restart — giống `.seed_state.json`.
"""

import json
import logging
import os

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from channels import get_channel, channels_for_track
from storage.database import (
    get_video, claim_video_status, update_video_status, update_video_metadata,
)

logger = logging.getLogger(__name__)

_STATE_FILE = os.path.join(os.path.dirname(__file__), ".review_state.json")

# Field cho nút ✏️ — key ngắn (callback_data giới hạn 64 byte) → cột DB.
# Cột DB phải nằm trong database._VIDEO_METADATA_FIELDS.
EDITABLE_FIELDS = {
    "title": ("youtube_title", "Tiêu đề YouTube"),
    "desc": ("youtube_description", "Mô tả YouTube"),
    "cap": ("tiktok_caption", "Caption TikTok"),
    "tags": ("tiktok_hashtags", "Hashtags TikTok"),
}

_CALLBACK_PREFIX = "rv"


# --- State (persisted qua restart, như seed_bot) ---

def _get_state() -> dict | None:
    if not os.path.exists(_STATE_FILE):
        return None
    try:
        with open(_STATE_FILE) as f:
            return json.load(f) or None
    except (json.JSONDecodeError, OSError):
        return None


def _set_state(state: dict | None) -> None:
    try:
        if state is None:
            if os.path.exists(_STATE_FILE):
                os.remove(_STATE_FILE)
            return
        with open(_STATE_FILE, "w") as f:
            json.dump(state, f)
    except OSError as e:
        logger.warning("Cannot persist review state: %s", e)


def skip_awaiting() -> str | None:
    """Huỷ state đang chờ (lệnh /skip). Returns reply, None nếu không có state."""
    state = _get_state()
    if state is None:
        return None
    _set_state(None)
    return "✅ Đã bỏ qua."


# --- Push review ---

def review_keyboard(video_id: int) -> dict:
    return {"inline_keyboard": [
        [
            {"text": "✅ Duyệt", "callback_data": f"{_CALLBACK_PREFIX}:a:{video_id}"},
            {"text": "❌ Loại", "callback_data": f"{_CALLBACK_PREFIX}:r:{video_id}"},
        ],
        [
            {"text": "✏️ Sửa metadata", "callback_data": f"{_CALLBACK_PREFIX}:e:{video_id}"},
        ],
    ]}


def _story_hook(video: dict) -> str:
    """Hook từ rewritten_content của story gốc (nếu là video Drama)."""
    story_id = video.get("story_id")
    if not story_id:
        return ""
    try:
        from storage.stories import get_story
        story = get_story(story_id)
        rewritten = json.loads(story.get("rewritten_content") or "{}") if story else {}
        return rewritten.get("hook", "")
    except Exception:
        return ""


def push_review(video_id: int) -> bool:
    """Gửi preview + inline keyboard cho reviewer, set status pending_approval.

    Giữ nguyên triết lý issue #60: script text là review artifact chính —
    video preview gửi hỏng/quá cỡ (kể cả sau khi nén) thì reviewer vẫn duyệt
    được bằng script + nút bấm.
    """
    from notifier import telegram_bot

    video = get_video(video_id)
    if not video:
        logger.error("push_review: video %d not found", video_id)
        return False
    video_path = video.get("video_path")
    if not video_path or not os.path.exists(video_path):
        logger.error("push_review: file missing for video %d: %s", video_id, video_path)
        return False

    destination = video.get("destination") or ""
    channel_name = ""
    if destination:
        try:
            channel_name = get_channel(destination)["name"]
        except ValueError:
            logger.warning("Video %d has unknown destination %r", video_id, destination)
    hook = _story_hook(video)
    title = video.get("youtube_title", "") or video.get("tiktok_caption", "")

    # 1. Script = review artifact chính (như send_video_for_approval cũ).
    script_sent = False
    script_text = video.get("script_text", "")
    if script_text:
        word_count = len(script_text.split())
        script_sent = telegram_bot._send_text(
            f"📋 SCRIPT VIDEO #{video_id} — ĐỌC VÀ DUYỆT\n"
            f"{'=' * 30}\n"
            f"📝 Tiêu đề: {title}\n"
            f"📊 Độ dài: {word_count} từ\n"
            f"{'=' * 30}\n\n"
            f"{script_text}"
        )

    # 2. Video preview (nén nếu >50MB) + nút bấm.
    from video.preview import compress_for_preview
    preview_path = compress_for_preview(video_path)
    caption_lines = [f"🎬 VIDEO #{video_id} CHỜ DUYỆT"]
    if video.get("story_id"):
        caption_lines.append(f"📖 Story #{video['story_id']}")
    if destination:
        caption_lines.append(
            f"📺 Kênh: {destination}" + (f" ({channel_name})" if channel_name else ""))
    if hook:
        caption_lines.append(f"🪝 Hook: {hook}")
    if title:
        caption_lines.append(f"📝 Tiêu đề: {title}")
    if preview_path and preview_path != video_path:
        caption_lines.append("ℹ️ Bản preview đã nén — file gốc dùng để upload.")
    caption = "\n".join(caption_lines)

    msg_id = None
    if preview_path:
        msg_id = telegram_bot._send_video_file(
            preview_path, caption, reply_markup=review_keyboard(video_id))
    keyboard_delivered = bool(msg_id)
    if msg_id:
        from storage.database import update_video_telegram_id
        update_video_telegram_id(video_id, str(msg_id))
    else:
        # Video không gửi được — nút bấm vẫn phải tới tay reviewer.
        keyboard_delivered = telegram_bot.send_message_with_keyboard(
            caption + "\n\n⚠️ Không gửi được file video qua Telegram — "
                      "duyệt dựa trên script ở trên.",
            review_keyboard(video_id),
        )

    # Reviewability = INLINE KEYBOARD đã tới tay reviewer. Khác tiêu chí cũ
    # (script HOẶC video, issue #60): với review gate, nút ✅ là actuator duy
    # nhất route đúng qua scheduler — script suông không duyệt được (finding
    # Codex review PR #70). Không gửi được keyboard → giữ status để
    # main_drama._repush_stuck_reviews() thử lại lần chạy sau.
    if keyboard_delivered:
        update_video_status(video_id, "pending_approval")
        logger.info("Video %d pushed for review (video_sent=%s, script_sent=%s)",
                    video_id, bool(msg_id), script_sent)
        return True
    logger.error("Video %d: could not deliver review keyboard — staying put", video_id)
    return False


# --- Callback handling (nút bấm) ---

def handle_callback(data: str) -> tuple[str, dict | None]:
    """Xử lý callback_data `rv:*`. Returns (reply_text, keyboard | None).

    Pure DB/state — network (answerCallbackQuery, sendMessage) do
    telegram_bot lo, nên unit-test không cần mock HTTP.
    """
    parts = (data or "").split(":")
    if len(parts) < 3 or parts[0] != _CALLBACK_PREFIX:
        return "⚠️ Callback không hợp lệ.", None
    action = parts[1]
    try:
        video_id = int(parts[2])
    except ValueError:
        return "⚠️ Callback không hợp lệ.", None

    if action == "a":
        return _approve(video_id), None
    if action == "r":
        return _reject(video_id), None
    if action == "e":
        if not get_video(video_id):
            return f"⚠️ Video {video_id} không tồn tại.", None
        keyboard = {"inline_keyboard": [[
            {"text": label, "callback_data": f"{_CALLBACK_PREFIX}:ef:{video_id}:{key}"}
        ] for key, (_, label) in EDITABLE_FIELDS.items()]}
        return f"✏️ Video #{video_id} — chọn field cần sửa:", keyboard
    if action == "ef" and len(parts) >= 4:
        field_key = parts[3]
        if field_key not in EDITABLE_FIELDS:
            return "⚠️ Field không hợp lệ.", None
        video = get_video(video_id)
        if not video:
            return f"⚠️ Video {video_id} không tồn tại.", None
        column, label = EDITABLE_FIELDS[field_key]
        current = video.get(column, "") or "(trống)"
        _set_state({"mode": "edit", "video_id": video_id, "field": field_key})
        return (f"✏️ {label} hiện tại:\n{current}\n\n"
                f"Nhập giá trị mới (hoặc /skip để giữ nguyên):"), None
    return "⚠️ Callback không hợp lệ.", None


def _route_all(video: dict, header: str) -> str:
    """Route video tới MỌI kênh đích và trả về tóm tắt từng kênh.

    Dùng chung cho duyệt tay (`_approve`) lẫn tự phát hành (`auto_dispatch`):
    YouTube → xếp lịch CADENCE, TikTok → gửi Telegram (Bé MC) upload tay. Mỗi
    kênh route độc lập — lỗi 1 kênh chỉ báo lại kèm lệnh xếp tay, không nổ cả
    hàm (video đã claim 'approved' trước khi gọi).
    """
    video_id = video["id"]
    lines = [header]
    for channel_key in _destinations_for(video):
        # get_channel nằm TRONG try: destination sai/cũ (không còn trong
        # registry) chỉ làm hỏng kênh đó và được báo lại kèm lệnh xếp lịch
        # tay, không nổ cả hàm sau khi video đã claim 'approved'.
        try:
            channel = get_channel(channel_key)
            lines.append(_route_to_channel(video, channel_key, channel))
        except Exception as e:
            logger.exception("Routing video %d to %s failed", video_id, channel_key)
            lines.append(
                f"  ❌ {channel_key}: lỗi xếp lịch — {e}\n"
                f"     Xếp tay: python -m scheduler.post_scheduler "
                f"schedule {video_id} <channel_key>")
    if len(lines) == 1:
        lines.append("  ⚠️ Không xác định được kênh đích — xếp lịch tay: "
                     f"python -m scheduler.post_scheduler schedule {video_id} <channel_key>")
    return "\n".join(lines)


def _approve(video_id: int) -> str:
    """✅: claim pending_approval → approved, rồi xếp lịch/queue mọi kênh đích.

    Từ khi bật tự phát hành (`auto_dispatch`), video MỚI không còn vào
    'pending_approval' nữa — hàm này giữ lại cho video cũ đang chờ duyệt và cho
    nút ✅ thủ công (vd duyệt lại video 'needs_review').
    """
    video = get_video(video_id)
    if not video:
        return f"⚠️ Video {video_id} không tồn tại."
    if not claim_video_status(video_id, "approved", "pending_approval"):
        current = get_video(video_id)
        return (f"⚠️ Video {video_id} không ở trạng thái chờ duyệt "
                f"(status={current.get('status') if current else '?'}).")
    return _route_all(video, f"✅ Video {video_id} đã duyệt.")


def _safe_platform(channel_key: str) -> str:
    """Platform của kênh, "" nếu key lạ (không nổ khi dò destination cũ)."""
    try:
        return get_channel(channel_key)["platform"]
    except Exception:
        return ""


def auto_dispatch(video_id: int) -> bool:
    """Tự phát hành video vừa render — KHÔNG chờ duyệt tay.

    Mô hình vận hành (theo yêu cầu chủ kênh): **YouTube tự đăng theo CADENCE**
    (`post_scheduler`, tick tự upload), **TikTok gửi Telegram (Bé MC) đăng tay**.
    Thay cho `push_review` (nút ✅ chặn) ở bước render của main.py/main_drama.py.

    - Claim 'ready' → 'approved' (idempotent: chạy lại sau crash KHÔNG route
      trùng vì video đã rời 'ready'; cũng chống re-dispatch bởi stuck-handler).
    - Route mọi kênh đích qua `_route_all` (dùng chung với `_approve`).
    - Gửi 1 tin Telegram **FYI (không nút chặn)**: đính kèm preview nếu video
      CHƯA tới Telegram qua đường TikTok tay (tránh gửi trùng file nặng); còn
      lại chỉ tóm tắt nơi đã route.

    Returns True nếu vừa dispatch; False nếu video không ở 'ready' (đã dispatch
    rồi / trạng thái khác) hoặc không tồn tại — caller không log nhầm là lỗi.
    """
    video = get_video(video_id)
    if not video:
        logger.error("auto_dispatch: video %d không tồn tại", video_id)
        return False
    if not claim_video_status(video_id, "approved", "ready"):
        current = get_video(video_id)
        logger.info("auto_dispatch: video %d không ở 'ready' (status=%s) — bỏ qua",
                    video_id, current.get("status") if current else "?")
        return False

    dests = _destinations_for(video)
    summary = _route_all(video, f"🚀 Video {video_id} đã tự phát hành:")
    already_in_telegram = any(_safe_platform(k) == "tiktok" for k in dests)
    _send_dispatch_fyi(video, summary, include_preview=not already_in_telegram)
    logger.info("Video %d auto-dispatched (dests=%s)", video_id, dests)
    return True


def _send_dispatch_fyi(video: dict, summary: str, include_preview: bool) -> None:
    """Gửi thông báo FYI sau khi tự phát hành (không nút bấm). Best-effort —
    nuốt lỗi notifier để không phá bước render (video đã 'approved' + đã route).
    """
    from notifier import telegram_bot
    try:
        if not include_preview:
            telegram_bot._send_text(summary)
            return
        video_path = video.get("video_path")
        if not video_path or not os.path.exists(video_path):
            telegram_bot._send_text(summary)
            return
        try:
            from video.preview import compress_for_preview
            preview_path = compress_for_preview(video_path)
        except Exception:
            preview_path = video_path
        caption = summary
        if preview_path and preview_path != video_path:
            caption += "\nℹ️ Bản preview đã nén — file gốc dùng để upload."
        msg_id = (telegram_bot._send_video_file(preview_path, caption)
                  if preview_path else None)
        if not msg_id:
            telegram_bot._send_text(summary)
    except Exception as e:
        logger.warning("auto_dispatch FYI cho video %s lỗi (non-fatal): %s",
                       video.get("id"), e)


def _route_to_channel(video: dict, channel_key: str, channel: dict) -> str:
    """Định tuyến 1 kênh sau khi duyệt.

    - TikTok: gửi video qua Telegram tới kênh Bé MC để upload TAY — KHÔNG
      auto-schedule/auto-upload (mô hình TikTok mới). Bé MC tự đăng.
    - YouTube (và platform khác): xếp lịch upload theo cadence qua post_scheduler.
    """
    if channel["platform"] == "tiktok":
        from notifier.telegram_bot import send_tiktok_manual
        if send_tiktok_manual(video["id"]):
            return f"  📲 {channel_key}: đã gửi video qua Telegram (Bé MC) để upload tay"
        return f"  ❌ {channel_key}: gửi video tới Bé MC thất bại (xem log)"

    from scheduler.post_scheduler import schedule_video
    post = schedule_video(video["id"], channel_key)
    if post:
        return f"  🗓 {channel_key}: đăng lúc {post['scheduled_at']}"
    return f"  ❌ {channel_key}: không xếp được lịch (xem log)"


def _destinations_for(video: dict) -> list[str]:
    """Các channel key video này sẽ đi tới sau khi duyệt.

    - `destination` (đã set lúc render) luôn được tôn trọng.
    - Video SHORT còn được nhân bản sang các kênh TikTok nhận track đó
      (channels.py: tiktok_main là account mixed) — đúng cadence doc:
      drama shorts → drama_youtube + tiktok.
    - Không có destination → suy từ track qua channel registry.
    """
    track = video.get("track") or "ai"
    is_short = video.get("video_type") == "short"
    result = []
    destination = video.get("destination")
    if destination:
        result.append(destination)
        for key, channel in channels_for_track(track).items():
            if key != destination and channel["platform"] == "tiktok" \
                    and is_short and channel["format_shorts"]:
                result.append(key)
        return result
    for key, channel in channels_for_track(track).items():
        if is_short and channel["format_shorts"]:
            result.append(key)
        elif not is_short and channel["format_long"]:
            result.append(key)
    return result


def _reject(video_id: int) -> str:
    """❌: đánh dấu rejected rồi chờ 1 message lý do (lưu vào review_note)."""
    video = get_video(video_id)
    if not video:
        return f"⚠️ Video {video_id} không tồn tại."
    if not claim_video_status(video_id, "rejected", "pending_approval"):
        current = get_video(video_id)
        return (f"⚠️ Video {video_id} không ở trạng thái chờ duyệt "
                f"(status={current.get('status') if current else '?'}).")
    _set_state({"mode": "reject_reason", "video_id": video_id})
    return (f"❌ Video {video_id} đã loại.\n"
            f"Nhập lý do để lưu lại (hoặc /skip để bỏ qua):")


# --- Plain-message handling (FSM đang chờ input) ---

def handle_awaiting_message(text: str) -> str | None:
    """Xử lý message thường khi đang chờ input. None = không có state chờ
    (telegram_bot sẽ chuyển tiếp cho seed_bot)."""
    state = _get_state()
    if state is None:
        return None
    video_id = state.get("video_id")
    mode = state.get("mode")

    if mode == "reject_reason":
        _set_state(None)
        try:
            update_video_metadata(video_id, review_note=text.strip())
        except Exception as e:
            logger.error("Cannot save reject reason for video %s: %s", video_id, e)
            return "⚠️ Không lưu được lý do (xem log)."
        return f"📝 Đã lưu lý do loại video {video_id}."

    if mode == "edit":
        field_key = state.get("field", "")
        if field_key not in EDITABLE_FIELDS:
            _set_state(None)
            return "⚠️ State sửa metadata không hợp lệ — đã huỷ."
        column, label = EDITABLE_FIELDS[field_key]
        _set_state(None)
        try:
            update_video_metadata(video_id, **{column: text.strip()})
        except Exception as e:
            logger.error("Cannot update %s for video %s: %s", column, video_id, e)
            return "⚠️ Không cập nhật được (xem log)."
        return f"✅ Đã cập nhật {label} cho video {video_id}:\n{text.strip()}"

    _set_state(None)
    return None


def help_text() -> str:
    return (
        "🎬 Review gate (Phase 5):\n"
        "Video mới render sẽ được gửi kèm nút ✅/❌/✏️.\n"
        "✅ Duyệt → tự xếp lịch đăng theo cadence (không đăng ngay)\n"
        "❌ Loại → hỏi lý do (lưu vào review_note)\n"
        "✏️ Sửa metadata → chọn field rồi nhập giá trị mới\n"
        "/skip — bỏ qua câu hỏi đang chờ trả lời"
    )
