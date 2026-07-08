from __future__ import annotations

"""
Analytics bot handlers (Phase 6 EPIC #6.1) — lệnh Telegram để nạp CSV số liệu
TikTok từ TikTok Studio.

Giống seed_bot.py / review_bot.py: đây là các hàm xử lý THUẦN, được
notifier/telegram_bot.py dispatch vào CÙNG vòng long-polling / 1 bot token (2
process cùng token → 409 Conflict). Trạng thái "đang chờ file CSV" lưu ở
notifier/.analytics_state.json (persisted qua restart).

Luồng:
  /import_tiktok_csv → bot chờ → user đính kèm file .csv → telegram_bot tải file
  về rồi gọi handle_csv_document(text) → parse + upsert → trả summary.
"""

import json
import logging
import os

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from analytics.tiktok_csv import import_csv_text

logger = logging.getLogger(__name__)

_STATE_FILE = os.path.join(os.path.dirname(__file__), ".analytics_state.json")


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
        logger.warning("Could not persist analytics bot state: %s", e)


def start_import_tiktok_csv() -> str:
    """Handle /import_tiktok_csv — chờ file CSV đính kèm tiếp theo."""
    _set_awaiting("tiktok_csv")
    return (
        "📎 Hãy đính kèm file CSV export từ TikTok Studio "
        "(Analytics → Export). File tiếp theo bạn gửi sẽ được nạp vào DB.\n"
        "Gõ /skip để huỷ."
    )


def is_awaiting_csv() -> bool:
    return _get_awaiting() == "tiktok_csv"


def skip_awaiting() -> str | None:
    """Huỷ trạng thái chờ (dùng chung cơ chế /skip). None nếu không có gì chờ."""
    if _get_awaiting() is None:
        return None
    _set_awaiting(None)
    return "✨ Đã huỷ nạp CSV TikTok."


def handle_csv_document(text: str, file_name: str = "") -> str:
    """Nạp nội dung CSV (đã tải về) nếu đang chờ. Returns reply, luôn clear state.

    Chỉ được gọi khi is_awaiting_csv() True (telegram_bot kiểm tra trước khi
    tải file — tránh tải nhầm mọi document người dùng gửi).
    """
    _set_awaiting(None)
    if file_name and not file_name.lower().endswith(".csv"):
        return (f"⚠️ File '{file_name}' không phải .csv. "
                f"Gõ /import_tiktok_csv để thử lại.")
    try:
        summary = import_csv_text(text)
    except Exception as e:
        logger.exception("TikTok CSV import failed")
        return f"⚠️ Lỗi nạp CSV: {e}"
    return (
        f"✅ Đã nạp CSV TikTok:\n"
        f"  • {summary['imported']} video có số liệu\n"
        f"  • {summary['skipped']} dòng bỏ qua (thiếu id/số liệu)\n"
        f"Tổng {summary['rows']} dòng."
    )


def help_text() -> str:
    return (
        "📊 Analytics bot:\n"
        "/import_tiktok_csv — Nạp CSV số liệu từ TikTok Studio"
    )
