from __future__ import annotations

"""
Seed Bot — nhận lệnh Telegram để feed nội dung nguồn cho track Drama (Phase 2).

Thiết kế khác với phase-2-detailed.md mục 3.2: tài liệu đề xuất chạy 2 process
Telegram bot độc lập (`python-telegram-bot`, FSM riêng). Thực tế module này
được gọi trực tiếp từ `notifier/telegram_bot.py._handle_update()` — CÙNG một
vòng long-polling/1 bot token đang chạy sẵn cho approve/reject, thay vì một
poller thứ hai.

Lý do: Telegram's `getUpdates` chỉ cho phép MỘT long-poll connection tại một
thời điểm cho mỗi bot token — chạy 2 process độc lập cùng gọi `getUpdates`
trên cùng 1 token sẽ gây lỗi 409 Conflict liên tục (đúng cơ chế mà
`_acquire_bot_lock()` trong telegram_bot.py đã cố tránh cho CHÍNH bot đó, nói
gì đến 2 bot khác nhau tranh nhau cùng token). Module này export các hàm xử
lý lệnh THUẦN (không tự polling), để telegram_bot.py dispatch vào cùng vòng
lặp hiện có — tránh bug 409 mà vẫn đạt được mục tiêu chức năng của Phase 2.

Nếu sau này có bot token riêng cho seed bot, có thể tách thành process độc
lập thật sự mà không cần đổi các hàm xử lý lệnh bên dưới.
"""

import json
import logging
import os
import re
import uuid

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from storage.stories import insert_story, get_pending

logger = logging.getLogger(__name__)

# Persisted so a bot restart mid-conversation (rare, but possible) doesn't
# leave /seed_vn or /seed_url silently swallowing the next unrelated message.
_STATE_FILE = os.path.join(os.path.dirname(__file__), ".seed_state.json")

_META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_OG_PROPERTY_RE = re.compile(r'property=["\']og:([a-zA-Z]+)["\']', re.IGNORECASE)
_CONTENT_RE = re.compile(r'content=["\']([^"\']*)["\']', re.IGNORECASE)


# --- Conversation state (single-user bot: one awaiting state at a time) ---

def _get_awaiting() -> str | None:
    if not os.path.exists(_STATE_FILE):
        return None
    try:
        with open(_STATE_FILE) as f:
            return json.load(f).get("awaiting")
    except (json.JSONDecodeError, OSError):
        return None


def _set_awaiting(value: str | None):
    try:
        with open(_STATE_FILE, "w") as f:
            json.dump({"awaiting": value}, f)
    except OSError as e:
        logger.warning("Could not persist seed bot state: %s", e)


# --- Public command handlers (called from telegram_bot._handle_update) ---

def start_seed_vn() -> str:
    """Handle /seed_vn — prompt for the next message to be saved as a seed."""
    _set_awaiting("vn_seed")
    return (
        "📝 Hãy gửi tình huống lõi (1-3 câu). "
        "Tin nhắn tiếp theo của bạn sẽ được lưu làm seed VN-original."
    )


def start_seed_url() -> str:
    """Handle /seed_url — prompt for the next message to be a FB/TikTok link."""
    _set_awaiting("seed_url")
    return "🔗 Hãy paste link Facebook/TikTok. Bot sẽ lưu link kèm metadata."


def handle_awaiting_message(text: str) -> str | None:
    """If /seed_vn or /seed_url is awaiting input, consume `text` as that input.

    Returns the reply text, or None if nothing was awaiting (caller should
    treat `text` as an ordinary/unrecognized message instead).
    """
    awaiting = _get_awaiting()
    if awaiting is None:
        return None
    _set_awaiting(None)
    if awaiting == "vn_seed":
        return _save_vn_seed(text)
    if awaiting == "seed_url":
        return _save_seed_url(text)
    return None


def list_pending_text(limit: int = 5) -> str:
    """Handle /list_pending — top N pending Drama stories, short markdown-ish list."""
    pending = get_pending(limit=limit, track="drama")
    if not pending:
        return "✨ Không có story Drama nào đang chờ duyệt."
    lines = [f"📋 {len(pending)} story đang chờ duyệt:"]
    for s in pending:
        title = s.get("title") or (s["raw_content"][:60] + "...")
        lines.append(f"  • #{s['id']} [{s['source']}] {title}")
    return "\n".join(lines)


def help_text() -> str:
    """Handle /help contribution for the seed bot commands."""
    return (
        "🎭 Drama seed bot:\n"
        "/seed_vn — Gửi tình huống lõi VN-original\n"
        "/seed_url — Gửi link FB/TikTok làm seed\n"
        "/list_pending — Xem story Drama đang chờ duyệt"
    )


# --- Internal ---

def _save_vn_seed(text: str) -> str:
    text = text.strip()
    if not text:
        return "⚠️ Nội dung trống, không lưu. Gõ /seed_vn để thử lại."
    source_id = f"vn_{uuid.uuid4().hex[:12]}"
    story_id = insert_story(
        source="vn_original", source_id=source_id, raw_content=text, track="drama",
    )
    logger.info("Saved VN-original seed as story %d", story_id)
    return f"✅ Đã lưu seed VN-original #{story_id}."


def _save_seed_url(url: str) -> str:
    url = url.strip()
    if not url.startswith("http"):
        return "⚠️ Không phải link hợp lệ. Gõ /seed_url để thử lại."
    og = _fetch_og_metadata(url)
    title = og.get("title", "")
    raw_content = og.get("description", "") or title or url
    source_id = f"url_{uuid.uuid4().hex[:12]}"
    story_id = insert_story(
        source="url_seed", source_id=source_id, raw_content=raw_content, track="drama",
        title=title or None,
        metadata={"url": url, "og_image": og.get("image")},
    )
    logger.info("Saved URL seed as story %d (%s)", story_id, url)
    reply = f"✅ Đã lưu seed từ link #{story_id}."
    if title:
        reply += f"\n📝 {title}"
    return reply


def _fetch_og_metadata(url: str) -> dict:
    """Best-effort Open Graph tag scrape (title/description/image).

    Uses `requests` (already a project dependency) + a tolerant regex parse
    instead of pulling in a new HTML-parsing dependency for 3 meta tags.
    Returns {} on any network/parse failure — a failed fetch must not block
    saving the seed URL itself.
    """
    import requests

    try:
        resp = requests.get(
            url, timeout=10,
            headers={"User-Agent": "Mozilla/5.0 (compatible; ContentPipelineBot/1.0)"},
        )
        resp.raise_for_status()
    except Exception as e:
        logger.warning("Failed to fetch OG metadata for %s: %s", url, e)
        return {}

    result = {}
    for tag in _META_TAG_RE.findall(resp.text):
        prop_match = _OG_PROPERTY_RE.search(tag)
        content_match = _CONTENT_RE.search(tag)
        if prop_match and content_match:
            result.setdefault(prop_match.group(1), content_match.group(1))
    return result
