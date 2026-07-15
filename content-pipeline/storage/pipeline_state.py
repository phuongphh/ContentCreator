from __future__ import annotations

"""
Storage helper cho bảng `pipeline_state` — kv scalar bền vững giữa các lần chạy
(migration 008, issue #90).

Dùng khi một collector/job cần nhớ 1 giá trị nhỏ qua các lần chạy (con trỏ
offset, id last-seen, timestamp) mà không muốn dựng bảng riêng hay file state
mong manh (file state không sống sót qua re-clone; DB thì có — cùng độ bền mà
drama pipeline đã dựa vào để resume-from-crash).

User đầu tiên: con trỏ import HuggingFace hàng ngày
(collectors/hf_drama_importer.import_daily) — mỗi ngày đi TỚI trong dump AITA
270K dòng thay vì nạp lại cùng một đuôi.

Value luôn lưu dạng TEXT; helper `get_int`/`set_int` bọc quy đổi cho con trỏ số.
"""

import logging
from typing import Optional

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from storage.database import get_connection

logger = logging.getLogger(__name__)


def get_state(key: str, default: Optional[str] = None) -> Optional[str]:
    """Đọc value TEXT theo key, hoặc `default` nếu chưa có."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT value FROM pipeline_state WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row is not None else default
    finally:
        conn.close()


def set_state(key: str, value: str) -> None:
    """Ghi (upsert) value TEXT cho key, cập nhật updated_at."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO pipeline_state (key, value, updated_at) "
            "VALUES (?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated_at = CURRENT_TIMESTAMP",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()


def get_int(key: str, default: int = 0) -> int:
    """Đọc con trỏ số. Value hỏng (không phải int) → `default` (self-healing)."""
    raw = get_state(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning("pipeline_state[%s]=%r không phải int — dùng default %d",
                       key, raw, default)
        return default


def set_int(key: str, value: int) -> None:
    """Ghi con trỏ số."""
    set_state(key, str(int(value)))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from storage.database import init_db
    from storage.migrate import migrate_up
    init_db()
    migrate_up()
    print("pipeline_state OK")
