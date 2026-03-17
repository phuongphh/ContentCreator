"""
Telegram Bot — Approval workflow cho video pipeline.

Chức năng:
1. Gửi video preview + metadata để duyệt
2. Nhận callback approve/reject qua polling
3. Sau khi approve → trigger publish

KHÔNG còn gửi báo cáo text nữa — output cuối cùng là VIDEO.
"""

import json
import logging
import time
from datetime import date
from urllib.request import Request, urlopen
from urllib.parse import quote

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
from storage.database import (
    get_video, update_video_status, update_video_telegram_id,
    update_video_publish_url, get_videos_by_status,
)

logger = logging.getLogger(__name__)

TELEGRAM_MAX_LENGTH = 4096


def send_video_for_approval(video_id: int) -> bool:
    """Send a video to Telegram for manual approval.

    Sends the video file + metadata message with approve/reject instructions.

    Args:
        video_id: ID of the video in the database.

    Returns:
        True if sent successfully.
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

    # Build approval message
    today = date.today().strftime("%d/%m/%Y")
    vtype = "DÀI (YouTube)" if video["video_type"] == "long" else "NGẮN (Shorts/TikTok)"
    platform = video.get("scheduled_platform", "")
    title = video.get("youtube_title", "") or video.get("tiktok_caption", "")

    caption = (
        f"🎬 VIDEO CHỜ DUYỆT — {today}\n\n"
        f"📌 Loại: {vtype}\n"
        f"📅 Lịch đăng: {video.get('scheduled_date', 'N/A')} → {platform}\n"
        f"📝 Tiêu đề: {title}\n\n"
        f"Script ({len(video['script_text'])} ký tự):\n"
        f"{video['script_text'][:500]}{'...' if len(video['script_text']) > 500 else ''}\n\n"
        f"💬 Trả lời tin nhắn này:\n"
        f"  ✅ /approve_{video_id} — Duyệt và tự động đăng\n"
        f"  ❌ /reject_{video_id} — Bỏ qua video này"
    )

    # Send video file with caption
    msg_id = _send_video_file(video_path, caption)

    if msg_id:
        update_video_telegram_id(video_id, str(msg_id))
        update_video_status(video_id, "pending_approval")
        logger.info("Video %d sent for approval (msg_id=%s)", video_id, msg_id)
        return True

    return False


def poll_approvals() -> list[dict]:
    """Poll Telegram for approval/rejection commands.

    Checks for messages matching /approve_<id> or /reject_<id>.

    Returns:
        List of actions: [{"video_id": int, "action": "approve"|"reject"}, ...]
    """
    if not config.TELEGRAM_BOT_TOKEN:
        return []

    updates = _get_updates()
    actions = []

    for update in updates:
        message = update.get("message", {})
        text = message.get("text", "").strip()
        chat_id = str(message.get("chat", {}).get("id", ""))

        # Only process from our configured chat
        if chat_id != config.TELEGRAM_CHAT_ID:
            continue

        if text.startswith("/approve_"):
            try:
                vid = int(text.split("_", 1)[1])
                actions.append({"video_id": vid, "action": "approve"})
                _send_text(f"✅ Video {vid} đã được duyệt! Đang upload...")
            except (ValueError, IndexError):
                pass

        elif text.startswith("/reject_"):
            try:
                vid = int(text.split("_", 1)[1])
                actions.append({"video_id": vid, "action": "reject"})
                _send_text(f"❌ Video {vid} đã bị từ chối.")
            except (ValueError, IndexError):
                pass

    return actions


def send_publish_notification(video_id: int, platform: str, url: str):
    """Notify via Telegram that a video has been published."""
    _send_text(
        f"🚀 Video {video_id} đã đăng lên {platform}!\n"
        f"🔗 {url}"
    )


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


# --- Internal helpers ---

def _send_video_file(video_path: str, caption: str) -> str | None:
    """Send a video file via Telegram sendVideo API."""
    import http.client
    import mimetypes

    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendVideo"

    boundary = "----FormBoundary7MA4YWxkTrZu0gW"
    body_parts = []

    # chat_id field
    body_parts.append(f"--{boundary}")
    body_parts.append('Content-Disposition: form-data; name="chat_id"')
    body_parts.append("")
    body_parts.append(config.TELEGRAM_CHAT_ID)

    # caption field (truncate to 1024)
    body_parts.append(f"--{boundary}")
    body_parts.append('Content-Disposition: form-data; name="caption"')
    body_parts.append("")
    body_parts.append(caption[:1024])

    # Construct text part
    text_body = "\r\n".join(body_parts).encode("utf-8")

    # Video file part header
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
                return str(result["result"]["message_id"])
            logger.error("Telegram sendVideo failed: %s", result)
            return None
    except Exception as e:
        logger.error("Failed to send video to Telegram: %s", e)
        return None


def _send_text(text: str) -> bool:
    """Send a text message via Telegram."""
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        return False

    url = (
        f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"
        f"/sendMessage?chat_id={config.TELEGRAM_CHAT_ID}"
        f"&text={quote(text[:TELEGRAM_MAX_LENGTH])}"
    )
    try:
        req = Request(url)
        with urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        logger.error("Telegram send failed: %s", e)
        return False


# Store last update_id to avoid processing same updates
_OFFSET_FILE = os.path.join(os.path.dirname(__file__), ".telegram_offset")


def _get_updates() -> list[dict]:
    """Get new updates from Telegram using long polling."""
    offset = 0
    if os.path.exists(_OFFSET_FILE):
        try:
            with open(_OFFSET_FILE) as f:
                offset = int(f.read().strip()) + 1
        except (ValueError, OSError):
            pass

    url = (
        f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"
        f"/getUpdates?timeout=5&offset={offset}"
    )

    try:
        req = Request(url)
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())

        if not data.get("ok"):
            return []

        updates = data.get("result", [])
        if updates:
            # Save last update_id
            last_id = updates[-1]["update_id"]
            with open(_OFFSET_FILE, "w") as f:
                f.write(str(last_id))

        return updates

    except Exception as e:
        logger.error("Telegram getUpdates failed: %s", e)
        return []


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Telegram approval bot ready.")
    print("Polling for commands...")
    while True:
        actions = poll_approvals()
        for action in actions:
            print(f"Action: {action}")
        time.sleep(5)
