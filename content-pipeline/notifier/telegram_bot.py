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
from urllib.parse import quote

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
from storage.database import (
    get_video, update_video_status, update_video_telegram_id,
    update_video_publish_url, get_videos_by_status,
)

logger = logging.getLogger(__name__)

TELEGRAM_MAX_LENGTH = 4096

# Store last update_id to avoid re-processing
_OFFSET_FILE = os.path.join(os.path.dirname(__file__), ".telegram_offset")


# --- Public API ---

def send_video_for_approval(video_id: int) -> bool:
    """Send a video to Telegram for manual approval."""
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

    caption = (
        f"🎬 VIDEO CHỜ DUYỆT — {today}\n\n"
        f"📌 Loại: {vtype}\n"
        f"📅 Lịch đăng: {video.get('scheduled_date', 'N/A')} → {platform}\n"
        f"📝 Tiêu đề: {title}\n\n"
        f"💬 Trả lời:\n"
        f"  ✅ /approve_{video_id}\n"
        f"  ❌ /reject_{video_id}"
    )

    msg_id = _send_video_file(video_path, caption)
    if msg_id:
        update_video_telegram_id(video_id, str(msg_id))
        update_video_status(video_id, "pending_approval")
        logger.info("Video %d sent for approval (msg_id=%s)", video_id, msg_id)
        return True
    return False


def send_publish_notification(video_id: int, platform: str, url: str):
    """Notify via Telegram that a video has been published."""
    _send_text(f"🚀 Video {video_id} đã đăng lên {platform}!\n🔗 {url}")


def send_narrative_report(narrative: str, article_count: int) -> bool:
    """Send the narrative summary as a text message before video generation.

    This ensures the user always receives the daily summary even if
    video generation fails downstream.
    """
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.warning("Telegram credentials not configured, skipping narrative.")
        return False

    today = date.today().strftime("%d/%m/%Y")
    header = f"📝 TÓM TẮT AI HÔM NAY — {today}\n({article_count} bài đã phân tích)\n\n"
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
            break
        except Exception as e:
            consecutive_errors += 1
            wait = min(2 ** consecutive_errors, 60)
            logger.error("Bot error (attempt %d): %s — retrying in %ds",
                         consecutive_errors, e, wait)
            time.sleep(wait)


def _handle_update(update: dict, publish_callback):
    """Process a single Telegram update."""
    message = update.get("message", {})
    text = message.get("text", "").strip()
    chat_id = str(message.get("chat", {}).get("id", ""))

    if chat_id != config.TELEGRAM_CHAT_ID:
        return

    if text.startswith("/approve_"):
        try:
            video_id = int(text.split("_", 1)[1])
            video = get_video(video_id)
            if not video:
                _send_text(f"⚠️ Video {video_id} không tồn tại.")
                return
            if video["status"] != "pending_approval":
                _send_text(f"⚠️ Video {video_id} không ở trạng thái chờ duyệt (status={video['status']}).")
                return

            update_video_status(video_id, "approved")
            _send_text(f"✅ Video {video_id} đã duyệt! Đang upload...")
            logger.info("Video %d approved — triggering publish", video_id)

            # Publish immediately
            publish_callback(video_id)

        except (ValueError, IndexError):
            _send_text("⚠️ Lệnh không hợp lệ. Dùng: /approve_<số>")

    elif text.startswith("/reject_"):
        try:
            video_id = int(text.split("_", 1)[1])
            update_video_status(video_id, "rejected")
            _send_text(f"❌ Video {video_id} đã bị từ chối.")
            logger.info("Video %d rejected", video_id)
        except (ValueError, IndexError):
            _send_text("⚠️ Lệnh không hợp lệ. Dùng: /reject_<số>")

    elif text == "/status":
        pending = get_videos_by_status("pending_approval")
        if pending:
            lines = ["⏳ Video đang chờ duyệt:"]
            for v in pending:
                title = v.get("youtube_title", "") or v.get("tiktok_caption", "")
                lines.append(f"  • ID {v['id']}: {title}")
            _send_text("\n".join(lines))
        else:
            _send_text("✨ Không có video nào đang chờ duyệt.")

    elif text == "/help":
        _send_text(
            "📖 Lệnh bot:\n"
            "/approve_<id> — Duyệt và đăng video\n"
            "/reject_<id> — Từ chối video\n"
            "/status — Xem video đang chờ duyệt"
        )


# --- Internal helpers ---

def _send_video_file(video_path: str, caption: str) -> str | None:
    """Send a video file via Telegram sendVideo API.

    Telegram caption limit is 1024 chars. If caption exceeds this,
    truncate at last newline and send the full caption as a follow-up text.
    """
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendVideo"

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


def _send_single_text(text: str) -> bool:
    """Send a single text message (must be <= 4096 chars)."""
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        return False

    if len(text) > TELEGRAM_MAX_LENGTH:
        logger.warning("Single text message exceeds %d chars (%d), truncating",
                        TELEGRAM_MAX_LENGTH, len(text))
        text = text[:TELEGRAM_MAX_LENGTH - 20] + "\n\n⚠️ (bị cắt ngắn)"

    url = (
        f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"
        f"/sendMessage?chat_id={config.TELEGRAM_CHAT_ID}"
        f"&text={quote(text)}"
    )
    try:
        req = Request(url)
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
