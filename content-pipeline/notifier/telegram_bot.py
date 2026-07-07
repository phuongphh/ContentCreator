from __future__ import annotations

"""
Telegram Bot — Persistent bot chạy long-polling.

Khi nhận /approve → publish ngay lập tức, không cần cronjob.

Chạy: python main.py --bot (chạy liên tục như daemon)
"""

import json
import logging
import os
import time
from datetime import date
from urllib.request import Request, urlopen

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
from storage.database import (
    get_video, update_video_status, update_video_telegram_id,
    update_video_publish_url, get_videos_by_status,
)

logger = logging.getLogger(__name__)

TELEGRAM_MAX_LENGTH = 4096
# Telegram Bot API caps file uploads at 50 MB. Beyond this, sendVideo is
# guaranteed to fail (Broken pipe / 413), so we skip the doomed upload instead
# of reading the whole file into memory and blocking ~120s on a dead request.
TELEGRAM_MAX_FILE_BYTES = 50 * 1024 * 1024

# Store last update_id to avoid re-processing
_OFFSET_FILE = os.path.join(os.path.dirname(__file__), ".telegram_offset")
# PID lock file — prevents duplicate bot instances causing 409 Conflict
_BOT_LOCK_FILE = os.path.join(os.path.dirname(__file__), ".bot.pid")


# --- Public API ---

def send_video_for_approval(video_id: int) -> bool:
    """Send a video and its script to Telegram for manual approval.

    The script text is the actual narration used in the video — this is the
    single source of truth that the reviewer must cross-check against the video.
    No separate summary is submitted; the script IS the review artifact.
    """
    video = get_video(video_id)
    if not video:
        logger.error("Video %d not found", video_id)
        return False

    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.warning("Telegram credentials not configured, skipping approval.")
        return False

    video_path = video.get("video_path")
    if not video_path or not os.path.exists(video_path):
        logger.error("Video file not found: %s", video_path)
        return False

    today = date.today().strftime("%d/%m/%Y")
    vtype = "DÀI (YouTube)" if video["video_type"] == "long" else "NGẮN (Shorts/TikTok)"
    platform = video.get("scheduled_platform", "")
    title = video.get("youtube_title", "") or video.get("tiktok_caption", "")
    # Let the reviewer know to check music levels/licensing when BGM is on.
    bgm_note = "\n🎵 Có nhạc nền — kiểm tra âm lượng & bản quyền." if getattr(
        config, "ENABLE_BGM", False) else ""

    # --- Step 1: Send the script text as the review artifact ---
    # The script IS the primary review artifact, so its successful delivery is
    # what makes the video reviewable — NOT the (size-limited, flaky) video
    # upload below. We capture the send result to drive the status transition.
    script_sent = False
    script_text = video.get("script_text", "")
    if script_text:
        word_count = len(script_text.split())
        script_header = (
            f"📋 SCRIPT VIDEO #{video_id} — ĐỌC VÀ DUYỆT\n"
            f"{'=' * 30}\n"
            f"📌 Loại: {vtype}\n"
            f"📝 Tiêu đề: {title}\n"
            f"📊 Độ dài: {word_count} từ\n"
            f"{'=' * 30}\n\n"
            f"⚠️ Đây là SCRIPT THẬT dùng trong video.\n"
            f"Hãy đọc kỹ và đối chiếu với video bên dưới.\n\n"
            f"{'─' * 30}\n\n"
            f"{script_text}\n\n"
            f"{'─' * 30}\n"
            f"📌 Xem video bên dưới rồi trả lời:\n"
            f"  ✅ /approve_{video_id}\n"
            f"  ❌ /reject_{video_id}"
        )
        script_sent = _send_text(script_header)
        if script_sent:
            logger.info("Script text sent for review (video %d, %d words)", video_id, word_count)
        else:
            logger.error("Failed to send script text for video %d", video_id)
    else:
        logger.warning("Video %d has no script_text — sending video only", video_id)

    # --- Step 2: Send the video file ---
    caption = (
        f"🎬 VIDEO CHỜ DUYỆT — {today}\n\n"
        f"📌 Loại: {vtype}\n"
        f"📅 Lịch đăng: {video.get('scheduled_date', 'N/A')} → {platform}\n"
        f"📝 Tiêu đề: {title}{bgm_note}\n\n"
        f"⚠️ Đối chiếu video này với script ở trên.\n"
        f"Script phải khớp 100% với nội dung video.\n\n"
        f"💬 Trả lời:\n"
        f"  ✅ /approve_{video_id}\n"
        f"  ❌ /reject_{video_id}"
    )

    # Video >50MB: nén 1 bản preview riêng (Phase 5 EPIC #5.1) thay vì bỏ qua
    # luôn như trước — reviewer được xem hình thật thay vì chỉ script. Nén
    # thất bại → preview_path=None → giữ nguyên fallback script-only cũ.
    from video.preview import compress_for_preview
    preview_path = compress_for_preview(video_path)
    if preview_path and preview_path != video_path:
        caption += "\nℹ️ Bản preview đã nén — file gốc dùng để upload."

    msg_id = _send_video_file(preview_path, caption) if preview_path else None
    if msg_id:
        update_video_telegram_id(video_id, str(msg_id))

    # --- Step 3: Set status based on whether the video is actually reviewable ---
    # The reviewer can act as long as they received the script OR the video.
    # Decoupling the status from the video upload fixes issue #60: a too-large
    # or broken-pipe video upload must NOT strand the video at status=ready,
    # which would make /approve_<id> fail with "không ở trạng thái chờ duyệt".
    reviewable = script_sent or bool(msg_id)
    if reviewable:
        update_video_status(video_id, "pending_approval")
        if not msg_id:
            # Script reached the reviewer but the video file did not. Tell them
            # explicitly so they don't wait for a video that will never arrive,
            # and remind them they can still act on the script alone.
            _send_text(
                f"⚠️ Không gửi được FILE VIDEO #{video_id} qua Telegram "
                f"(quá lớn >50MB hoặc lỗi mạng).\n"
                f"Script ở trên là bản duyệt chính — bạn vẫn có thể duyệt:\n"
                f"  ✅ /approve_{video_id}\n"
                f"  ❌ /reject_{video_id}"
            )
        logger.info(
            "Video %d set to pending_approval (video_file_sent=%s)", video_id, bool(msg_id)
        )
        return True

    # Neither the script nor the video reached the reviewer. Leave the status at
    # 'ready' so the run can be retried, and report failure upstream.
    logger.error(
        "Video %d: neither script nor video could be delivered — staying 'ready'", video_id
    )
    return False


def send_publish_notification(video_id: int, platform: str, url: str):
    """Notify via Telegram that a video has been published."""
    _send_text(f"🚀 Video {video_id} đã đăng lên {platform}!\n🔗 {url}")


def send_alert(text: str) -> bool:
    """Send an arbitrary alert/notification message.

    Public wrapper around the internal `_send_text` for callers outside this
    module (e.g. storage/collector_health.py's stale-collector check) that
    just need to push a plain text message, without reaching into a
    leading-underscore "private" helper.
    """
    return _send_text(text)


def send_narrative_report(narrative: str, article_count: int) -> bool:
    """Send the narrative summary as a text message before video generation.

    This ensures the user always receives the daily summary even if
    video generation fails downstream.
    """
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.warning("Telegram credentials not configured, skipping narrative.")
        return False

    today = date.today().strftime("%d/%m/%Y")
    header = (
        f"📝 TÓM TẮT AI HÔM NAY — {today}\n"
        f"({article_count} bài đã phân tích)\n\n"
        f"ℹ️ Đây là bản tóm tắt tham khảo — KHÔNG phải script video.\n"
        f"Script thật sẽ được gửi kèm video để duyệt.\n\n"
    )
    full_text = header + narrative

    # Telegram max is 4096 chars — split if needed
    if len(full_text) <= TELEGRAM_MAX_LENGTH:
        return _send_text(full_text)

    # Split into chunks at paragraph boundaries
    parts = _split_message(full_text)
    success = True
    for part in parts:
        if not _send_text(part):
            success = False
    return success


def _split_message(text: str, max_len: int = TELEGRAM_MAX_LENGTH) -> list[str]:
    """Split long text into chunks at paragraph boundaries."""
    if len(text) <= max_len:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break

        # Find last double-newline within limit
        split_at = text.rfind("\n\n", 0, max_len)
        if split_at == -1:
            # Fall back to single newline
            split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            # Last resort: hard cut
            split_at = max_len

        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")

    return chunks


def send_pipeline_summary(long_count: int, short_count: int, errors: list[str]):
    """Send a summary of the pipeline run."""
    today = date.today().strftime("%d/%m/%Y")
    lines = [f"📊 PIPELINE SUMMARY — {today}\n"]

    if long_count > 0:
        lines.append(f"🎬 Video dài: {long_count} video đã tạo")
    if short_count > 0:
        lines.append(f"📱 Video ngắn: {short_count} video đã tạo")
    if not long_count and not short_count:
        lines.append("⚠️ Không tạo được video nào hôm nay")

    pending = get_videos_by_status("pending_approval")
    if pending:
        lines.append(f"\n⏳ Đang chờ duyệt: {len(pending)} video")

    if errors:
        lines.append(f"\n⚠️ Lỗi ({len(errors)}):")
        for err in errors[:5]:
            lines.append(f"  • {err}")

    _send_text("\n".join(lines))


def _acquire_bot_lock() -> bool:
    """Write current PID to lock file. Return False if another instance is running."""
    if os.path.exists(_BOT_LOCK_FILE):
        try:
            with open(_BOT_LOCK_FILE) as f:
                existing_pid = int(f.read().strip())
            # Check if that PID is still alive
            try:
                os.kill(existing_pid, 0)  # signal 0 = existence check
                logger.warning(
                    "Another bot instance (PID %d) is already running — exiting to prevent 409 Conflict",
                    existing_pid,
                )
                return False
            except (ProcessLookupError, PermissionError):
                # PID not found or not ours — stale lock, overwrite it
                logger.info("Stale bot lock (PID %d) — overwriting", existing_pid)
        except (ValueError, OSError):
            pass  # Malformed or unreadable lock file — overwrite

    try:
        with open(_BOT_LOCK_FILE, "w") as f:
            f.write(str(os.getpid()))
    except OSError as e:
        logger.warning("Could not write bot lock file: %s", e)
    return True


def _release_bot_lock():
    """Remove PID lock file on clean shutdown."""
    try:
        if os.path.exists(_BOT_LOCK_FILE):
            os.remove(_BOT_LOCK_FILE)
    except OSError:
        pass


def run_bot(publish_callback):
    """Run persistent Telegram bot with long-polling.

    Listens for /approve_<id> and /reject_<id> commands.
    On approve → immediately calls publish_callback(video_id) to upload.

    Args:
        publish_callback: function(video_id) -> None, handles publishing.
    """
    if not config.TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not configured — cannot run bot")
        return

    if not _acquire_bot_lock():
        return  # Another instance running — exit cleanly (no 409)

    logger.info("🤖 Telegram bot started — listening for approvals...")
    _send_text("🤖 Bot đã khởi động. Sẵn sàng nhận lệnh approve/reject.")

    consecutive_errors = 0

    while True:
        try:
            updates = _get_updates(timeout=30)
            consecutive_errors = 0  # Reset on success

            for update in updates:
                _handle_update(update, publish_callback)

        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
            _send_text("🛑 Bot đã dừng.")
            _release_bot_lock()
            break
        except Exception as e:
            consecutive_errors += 1
            wait = min(2 ** consecutive_errors, 60)
            logger.error("Bot error (attempt %d): %s — retrying in %ds",
                         consecutive_errors, e, wait)
            time.sleep(wait)


def _handle_update(update: dict, publish_callback):
    """Process a single Telegram update."""
    # Review gate (Phase 5): nút inline ✅/❌/✏️ tới dưới dạng callback_query,
    # không phải message.
    if "callback_query" in update:
        _handle_callback_query(update["callback_query"])
        return

    message = update.get("message", {})
    text = message.get("text", "").strip()
    chat_id = str(message.get("chat", {}).get("id", ""))

    if chat_id != config.TELEGRAM_CHAT_ID:
        return

    # Plain (non-command) messages answer whichever conversation is awaiting
    # input: review gate FSM (reject reason / edit metadata, Phase 5) first,
    # then drama seed bot (/seed_vn, /seed_url — Phase 2). Commands below
    # always take priority even mid-conversation.
    if not text.startswith("/"):
        from notifier import review_bot, seed_bot
        reply = review_bot.handle_awaiting_message(text)
        if reply is None:
            reply = seed_bot.handle_awaiting_message(text)
        if reply is not None:
            _send_text(reply)
        return

    if text == "/skip":
        from notifier import review_bot
        reply = review_bot.skip_awaiting()
        _send_text(reply if reply is not None else "✨ Không có câu hỏi nào đang chờ.")
        return

    if text.startswith("/approve_"):
        try:
            video_id = int(text.split("_", 1)[1])
        except (ValueError, IndexError):
            _send_text("⚠️ Lệnh không hợp lệ. Dùng: /approve_<số>")
            return
        # Video của review gate (Phase 5, có destination trong channel
        # registry) phải đi qua scheduler routing — kể cả khi reviewer gõ
        # lệnh cũ thay vì bấm nút ✅. publish_video() cũ nhìn
        # scheduled_platform (rỗng với video drama) nên sẽ đăng vào hư không
        # và chặn luôn đường retry vì status đã 'approved'.
        if _is_review_gate_video(video_id):
            from notifier import review_bot
            reply, _ = review_bot.handle_callback(f"rv:a:{video_id}")
            _send_text(reply)
            return
        from video.review_service import approve
        # review_service performs the state transition + publish atomically.
        ok, msg = approve(video_id, publish_callback=publish_callback)
        _send_text(("✅ " if ok else "⚠️ ") + msg + (" Đang upload..." if ok else ""))

    elif text.startswith("/reject_"):
        try:
            video_id = int(text.split("_", 1)[1])
        except (ValueError, IndexError):
            _send_text("⚠️ Lệnh không hợp lệ. Dùng: /reject_<số>")
            return
        if _is_review_gate_video(video_id):
            from notifier import review_bot
            reply, _ = review_bot.handle_callback(f"rv:r:{video_id}")
            _send_text(reply)
            return
        from video.review_service import reject
        ok, msg = reject(video_id)
        _send_text(("❌ " if ok else "⚠️ ") + msg)

    elif text.startswith("/script_"):
        try:
            video_id = int(text.split("_", 1)[1])
            video = get_video(video_id)
            if not video:
                _send_text(f"⚠️ Video {video_id} không tồn tại.")
                return
            script_text = video.get("script_text", "")
            if not script_text:
                _send_text(f"⚠️ Video {video_id} không có script.")
                return
            vtype = "DÀI" if video["video_type"] == "long" else "NGẮN"
            title = video.get("youtube_title", "") or video.get("tiktok_caption", "")
            word_count = len(script_text.split())
            msg = (
                f"📋 SCRIPT VIDEO #{video_id}\n"
                f"📌 Loại: {vtype} | 📊 {word_count} từ\n"
                f"📝 Tiêu đề: {title}\n"
                f"{'─' * 30}\n\n"
                f"{script_text}"
            )
            _send_text(msg)
        except (ValueError, IndexError):
            _send_text("⚠️ Lệnh không hợp lệ. Dùng: /script_<số>")

    elif text == "/status":
        pending = get_videos_by_status("pending_approval")
        if pending:
            lines = ["⏳ Video đang chờ duyệt:"]
            for v in pending:
                title = v.get("youtube_title", "") or v.get("tiktok_caption", "")
                lines.append(f"  • ID {v['id']}: {title} → /script_{v['id']}")
            _send_text("\n".join(lines))
        else:
            _send_text("✨ Không có video nào đang chờ duyệt.")

    elif text == "/seed_vn":
        from notifier import seed_bot
        _send_text(seed_bot.start_seed_vn())

    elif text == "/seed_url":
        from notifier import seed_bot
        _send_text(seed_bot.start_seed_url())

    elif text == "/list_pending":
        from notifier import seed_bot
        _send_text(seed_bot.list_pending_text())

    elif text == "/help":
        from notifier import review_bot, seed_bot
        _send_text(
            "📖 Lệnh bot:\n"
            "/approve_<id> — Duyệt và đăng video\n"
            "/reject_<id> — Từ chối video\n"
            "/script_<id> — Xem lại script video\n"
            "/status — Xem video đang chờ duyệt\n\n"
            + review_bot.help_text() + "\n\n"
            + seed_bot.help_text()
        )


def _is_review_gate_video(video_id: int) -> bool:
    """Video thuộc flow review gate Phase 5 (route qua scheduler)?

    Phân biệt bằng `destination`: orchestrator mới (main_drama) luôn set
    destination từ channel registry; flow AI legacy để NULL và dùng
    scheduled_platform + publish ngay. Sai khác này giữ 2 flow không giẫm
    chân nhau khi cùng dùng lệnh /approve_<id>.

    Lỗi DB (thiếu bảng/cột trên DB chưa migrate) → False: rơi về flow legacy
    thay vì làm sập cả vòng xử lý update.
    """
    try:
        video = get_video(video_id)
    except Exception as e:
        logger.warning("Cannot check review-gate flag for video %d: %s", video_id, e)
        return False
    return bool(video and video.get("destination"))


def _handle_callback_query(callback_query: dict):
    """Dispatch một lần bấm nút inline (review gate, Phase 5).

    Luôn answerCallbackQuery (kể cả khi xử lý lỗi) để nút hết xoay vòng chờ
    trên client Telegram.
    """
    chat_id = str(callback_query.get("message", {}).get("chat", {}).get("id", ""))
    callback_id = callback_query.get("id", "")
    if chat_id != config.TELEGRAM_CHAT_ID:
        _answer_callback_query(callback_id)
        return

    data = callback_query.get("data", "")
    try:
        from notifier import review_bot
        reply, keyboard = review_bot.handle_callback(data)
    except Exception as e:
        logger.exception("Callback handling failed for %r", data)
        reply, keyboard = f"⚠️ Lỗi xử lý: {e}", None

    _answer_callback_query(callback_id)
    if keyboard:
        send_message_with_keyboard(reply, keyboard)
    else:
        _send_text(reply)


# --- Internal helpers ---

def _send_video_file(video_path: str, caption: str,
                     reply_markup: dict | None = None) -> str | None:
    """Send a video file via Telegram sendVideo API.

    Telegram caption limit is 1024 chars. If caption exceeds this,
    truncate at last newline and send the full caption as a follow-up text.

    `reply_markup`: inline keyboard dict (review gate, Phase 5), attached to
    the video message itself so the buttons sit under the preview.
    """
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendVideo"

    # Fail fast on oversized files: a >50MB upload is rejected by Telegram after
    # we have already read the whole file into RAM and blocked up to 120s on a
    # request that cannot succeed. Skipping it keeps the caller responsive and
    # lets it fall back to script-only review (issue #60).
    try:
        size = os.path.getsize(video_path)
    except OSError as e:
        logger.error("Cannot stat video file %s: %s", video_path, e)
        return None
    if size > TELEGRAM_MAX_FILE_BYTES:
        logger.error(
            "Video %s is %.1f MB, over Telegram's %d MB bot limit — skipping upload",
            video_path, size / 1024 / 1024, TELEGRAM_MAX_FILE_BYTES // 1024 // 1024,
        )
        return None

    # Telegram caption limit is 1024 chars
    caption_remainder = ""
    if len(caption) > 1024:
        # Truncate at last newline within limit, add continuation marker
        cut_at = caption.rfind("\n", 0, 1000)
        if cut_at == -1:
            cut_at = 1000
        caption_remainder = caption[cut_at:].lstrip("\n")
        caption = caption[:cut_at] + "\n\n⬇️ Xem tiếp bên dưới..."
        logger.info("Caption truncated at 1024 chars, will send remainder as text")

    boundary = "----FormBoundary7MA4YWxkTrZu0gW"
    body_parts = []

    body_parts.append(f"--{boundary}")
    body_parts.append('Content-Disposition: form-data; name="chat_id"')
    body_parts.append("")
    body_parts.append(config.TELEGRAM_CHAT_ID)

    body_parts.append(f"--{boundary}")
    body_parts.append('Content-Disposition: form-data; name="caption"')
    body_parts.append("")
    body_parts.append(caption[:1024])

    if reply_markup:
        body_parts.append(f"--{boundary}")
        body_parts.append('Content-Disposition: form-data; name="reply_markup"')
        body_parts.append("")
        body_parts.append(json.dumps(reply_markup))

    text_body = "\r\n".join(body_parts).encode("utf-8")

    filename = os.path.basename(video_path)
    file_header = (
        f"\r\n--{boundary}\r\n"
        f'Content-Disposition: form-data; name="video"; filename="{filename}"\r\n'
        f"Content-Type: video/mp4\r\n\r\n"
    ).encode("utf-8")

    with open(video_path, "rb") as f:
        file_data = f.read()

    closing = f"\r\n--{boundary}--\r\n".encode("utf-8")
    full_body = text_body + file_header + file_data + closing

    try:
        req = Request(
            url,
            data=full_body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        with urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode())
            if result.get("ok"):
                msg_id = str(result["result"]["message_id"])
                # Send remainder of caption as follow-up text if it was truncated
                if caption_remainder:
                    _send_text_chunks(f"📝 (tiếp theo)\n\n{caption_remainder}")
                return msg_id
            logger.error("Telegram sendVideo failed: %s", result)
            return None
    except Exception as e:
        logger.error("Failed to send video to Telegram: %s", e)
        return None


def _send_text_chunks(text: str) -> bool:
    """Send a text message via Telegram, splitting into multiple messages if needed.

    Adds [n/total] markers when splitting so user knows the message continues.
    """
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        return False

    # Reserve space for markers like "[2/3]\n" (~10 chars) to avoid overflow
    marker_reserve = 15
    chunks = _split_message(text, max_len=TELEGRAM_MAX_LENGTH - marker_reserve)
    total = len(chunks)
    success = True

    for i, chunk in enumerate(chunks):
        if total > 1:
            chunk = f"[{i + 1}/{total}]\n{chunk}" if i > 0 else f"{chunk}\n\n[1/{total}] ⬇️"
        if not _send_single_text(chunk):
            success = False

    return success


def _send_text(text: str) -> bool:
    """Send a text message via Telegram.

    Auto-splits into multiple messages if text exceeds 4096 chars.
    """
    return _send_text_chunks(text)


def send_message_with_keyboard(text: str, keyboard: dict) -> bool:
    """Send a text message with an inline keyboard (review gate, Phase 5).

    Text quá 4096 ký tự bị cắt (giữ keyboard) — caller của review gate chỉ
    gửi text ngắn nên thực tế không chạm giới hạn này.
    """
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        return False
    if len(text) > TELEGRAM_MAX_LENGTH:
        text = text[:TELEGRAM_MAX_LENGTH - 20] + "\n\n⚠️ (bị cắt ngắn)"

    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": text,
        "reply_markup": keyboard,
    }).encode("utf-8")
    try:
        req = Request(url, data=payload,
                      headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        logger.error("Telegram send (keyboard) failed: %s", e)
        return False


def _answer_callback_query(callback_id: str, text: str = "") -> bool:
    """Acknowledge một callback_query để nút inline hết trạng thái loading."""
    if not callback_id or not config.TELEGRAM_BOT_TOKEN:
        return False
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/answerCallbackQuery"
    payload = json.dumps({"callback_query_id": callback_id, "text": text}).encode("utf-8")
    try:
        req = Request(url, data=payload,
                      headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        logger.error("answerCallbackQuery failed: %s", e)
        return False


def _send_single_text(text: str) -> bool:
    """Send a single text message (must be <= 4096 chars).

    Uses POST with JSON body instead of GET with URL query params,
    because Vietnamese text URL-encoded via quote() can expand 3-6x
    in byte length, exceeding HTTP URL length limits (~8KB).
    """
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        return False

    if len(text) > TELEGRAM_MAX_LENGTH:
        logger.warning("Single text message exceeds %d chars (%d), truncating",
                        TELEGRAM_MAX_LENGTH, len(text))
        text = text[:TELEGRAM_MAX_LENGTH - 20] + "\n\n⚠️ (bị cắt ngắn)"

    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": text,
    }).encode("utf-8")
    try:
        req = Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        logger.error("Telegram send failed: %s", e)
        return False


def _get_updates(timeout: int = 30) -> list[dict]:
    """Get new updates from Telegram using long-polling.

    timeout=30 means Telegram holds the connection open for 30s
    if there are no updates, then returns empty. This is efficient
    and reacts instantly when a message arrives.
    """
    offset = 0
    if os.path.exists(_OFFSET_FILE):
        try:
            with open(_OFFSET_FILE) as f:
                offset = int(f.read().strip()) + 1
        except (ValueError, OSError):
            pass

    url = (
        f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"
        f"/getUpdates?timeout={timeout}&offset={offset}"
    )

    try:
        req = Request(url)
        # timeout + 5s buffer for network
        with urlopen(req, timeout=timeout + 5) as resp:
            data = json.loads(resp.read().decode())

        if not data.get("ok"):
            return []

        updates = data.get("result", [])
        if updates:
            last_id = updates[-1]["update_id"]
            with open(_OFFSET_FILE, "w") as f:
                f.write(str(last_id))

        return updates

    except Exception as e:
        logger.error("Telegram getUpdates failed: %s", e)
        return []


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Use: python main.py --bot")
