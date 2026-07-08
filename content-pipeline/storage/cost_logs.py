from __future__ import annotations

"""
Storage helper cho bảng `cost_logs` (Phase 6 — Analytics & Iteration).

Ghi TOKEN THÔ mỗi call AI (không phải $) — quy đổi ra tiền là overlay ở
analytics/pricing.py, cập nhật bảng giá không cần đụng dữ liệu lịch sử. Đây là
lý do processors/ai_usage.py cố ý không nhét pricing vào chỗ ghi log: token
thô không bao giờ "stale", tiền thì có.

`record_cost()` được thiết kế KHÔNG raise ra ngoài (nuốt lỗi, chỉ log) vì nó
được gọi trên đường nóng của pipeline AI — một DB chưa migrate 007 không được
làm hỏng cả lần chạy. Yêu cầu migration 007_analytics để thực sự ghi được.
"""

import logging
from datetime import datetime
from typing import Optional

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from storage.database import get_connection

logger = logging.getLogger(__name__)


def _today() -> str:
    return datetime.now().date().isoformat()


def record_cost(service: str, model: Optional[str] = None,
                label: Optional[str] = None,
                input_tokens: Optional[int] = None,
                output_tokens: Optional[int] = None,
                units: Optional[float] = None,
                ref_type: Optional[str] = None,
                ref_id: Optional[str] = None,
                date: Optional[str] = None) -> Optional[int]:
    """Ghi 1 dòng chi phí. Returns row id, hoặc None nếu ghi thất bại (non-fatal)."""
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO cost_logs (service, model, label, input_tokens, output_tokens, "
            "units, ref_type, ref_id, date) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (service, model, label, input_tokens, output_tokens, units,
             ref_type, str(ref_id) if ref_id is not None else None, date or _today()),
        )
        conn.commit()
        return cur.lastrowid
    except Exception as e:
        logger.debug("record_cost skipped (non-fatal): %s", e)
        return None
    finally:
        conn.close()


def daily_totals(since: Optional[str] = None) -> list[dict]:
    """Tổng token theo (date, service) từ `since`, mới → cũ. Cho dashboard cost tab."""
    conn = get_connection()
    try:
        if since:
            rows = conn.execute(
                "SELECT date, service, "
                "COALESCE(SUM(input_tokens), 0) AS input_tokens, "
                "COALESCE(SUM(output_tokens), 0) AS output_tokens, "
                "COUNT(*) AS calls FROM cost_logs WHERE date >= ? "
                "GROUP BY date, service ORDER BY date DESC, service",
                (since,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT date, service, "
                "COALESCE(SUM(input_tokens), 0) AS input_tokens, "
                "COALESCE(SUM(output_tokens), 0) AS output_tokens, "
                "COUNT(*) AS calls FROM cost_logs "
                "GROUP BY date, service ORDER BY date DESC, service",
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def rows_since(since: str) -> list[dict]:
    """Mọi dòng cost thô kể từ `since` (cho pricing overlay tính $ theo model)."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM cost_logs WHERE date >= ? ORDER BY date, id", (since,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    for row in daily_totals():
        print(row)
