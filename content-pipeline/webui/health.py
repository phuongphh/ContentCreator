from __future__ import annotations

"""
Health endpoint (Phase 5 EPIC #5.4) — mini HTTP server local trả `/health`
với trạng thái từng module: video/story theo status, queue scheduler, quota
YouTube hôm nay, last_success của collectors.

Chạy: python -m webui.health   (bind 127.0.0.1 ONLY — như webui/app.py,
không bao giờ expose public; Telegram daily report có thể kéo JSON từ đây).

`build_health_payload()` là pure DB nên unit-test được; mỗi section bọc
try/except riêng — DB chưa migrate đủ (thiếu bảng) chỉ làm section đó báo
lỗi chứ không 500 cả endpoint.
"""

import json
import logging
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
from storage.database import get_connection

logger = logging.getLogger(__name__)


def _count_by_status(table: str) -> dict:
    conn = get_connection()
    try:
        rows = conn.execute(
            f"SELECT status, COUNT(*) AS n FROM {table} GROUP BY status"
        ).fetchall()
        return {r["status"]: r["n"] for r in rows}
    finally:
        conn.close()


def _section(fn):
    """Chạy 1 section, lỗi → {'error': ...} thay vì sập cả payload."""
    try:
        return fn()
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def build_health_payload() -> dict:
    def videos():
        return _count_by_status("videos")

    def stories():
        return _count_by_status("stories")

    def scheduler():
        from storage import scheduled_posts
        counts = scheduled_posts.count_by_status()
        queued = scheduled_posts.get_by_status("queued", limit=1)
        return {
            "by_status": counts,
            "next_scheduled_at": queued[0]["scheduled_at"] if queued else None,
            "stale_uploading": len(scheduled_posts.get_stale_uploading()),
        }

    def quota():
        from storage.quota import units_used_today, quota_date
        used = units_used_today("youtube")
        return {
            "date_pt": quota_date(),
            "youtube_units_used": used,
            "youtube_units_limit": config.YOUTUBE_DAILY_QUOTA,
            "used_ratio": round(used / config.YOUTUBE_DAILY_QUOTA, 3),
        }

    def collectors():
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT name, last_success FROM collector_health"
            ).fetchall()
            return {r["name"]: r["last_success"] for r in rows}
        finally:
            conn.close()

    return {
        "generated_at": datetime.now().isoformat(sep=" ", timespec="seconds"),
        "videos": _section(videos),
        "stories": _section(stories),
        "scheduler": _section(scheduler),
        "quota": _section(quota),
        "collectors": _section(collectors),
    }


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (BaseHTTPRequestHandler API)
        if self.path.split("?")[0].rstrip("/") not in ("", "/health"):
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps(build_health_payload(), ensure_ascii=False,
                          indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        logger.debug("health: " + fmt, *args)


def run(port: int | None = None):
    port = port or config.HEALTH_PORT
    server = HTTPServer(("127.0.0.1", port), _HealthHandler)
    logger.info("Health endpoint on http://127.0.0.1:%d/health", port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
