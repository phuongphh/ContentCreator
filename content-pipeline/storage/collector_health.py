from __future__ import annotations

"""
Collector health tracking — Phase 2 EPIC #2.4 (Operational hardening).

Records the last successful run per collector (`record_success`) and lets a
separate cron job (this module's __main__) alert via Telegram if a collector
hasn't succeeded in N days — catching a stopped cron/launchd job or a
persistent uncaught crash, not day-to-day content variance (a collector that
runs and finds 0 eligible posts is still a "success": it did its job, there
just wasn't anything to collect that day).

Requires migration 003_collector_health (see storage/migrate.py).
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from storage.database import get_connection

logger = logging.getLogger(__name__)

DEFAULT_MAX_AGE_DAYS = 2.0


def record_success(name: str) -> None:
    """Mark `name` (e.g. 'reddit_drama') as having just succeeded."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO collector_health (name, last_success) VALUES (?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(name) DO UPDATE SET last_success = CURRENT_TIMESTAMP",
            (name,),
        )
        conn.commit()
        logger.info("Recorded successful run for collector %r", name)
    finally:
        conn.close()


def get_last_success(name: str) -> Optional[datetime]:
    """UTC datetime of the last recorded success, or None if never recorded."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT last_success FROM collector_health WHERE name = ?", (name,)
        ).fetchone()
    finally:
        conn.close()

    if row is None or row["last_success"] is None:
        return None
    raw = row["last_success"]
    fmt = "%Y-%m-%d %H:%M:%S.%f" if "." in raw else "%Y-%m-%d %H:%M:%S"
    try:
        return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
    except ValueError:
        logger.warning("Unparseable last_success timestamp for %r: %r", name, raw)
        return None


def is_stale(name: str, max_age_days: float = DEFAULT_MAX_AGE_DAYS) -> bool:
    """True if `name` has never succeeded, or its last success is older than max_age_days."""
    last = get_last_success(name)
    if last is None:
        return True
    age_days = (datetime.now(timezone.utc) - last).total_seconds() / 86400
    return age_days > max_age_days


def check_and_alert(names: list[str], max_age_days: float = DEFAULT_MAX_AGE_DAYS) -> list[str]:
    """Alert via Telegram for every stale collector in `names`. Returns the stale ones.

    Meant to run on its own cron (~every 12h), separate from the collectors
    themselves, so a collector that stops running entirely still gets caught.
    """
    from notifier.telegram_bot import send_alert

    stale = []
    for name in names:
        if is_stale(name, max_age_days):
            stale.append(name)
            logger.warning("Collector %r has not succeeded in > %.1f day(s)", name, max_age_days)
            send_alert(
                f"⚠️ Collector '{name}' chưa chạy thành công trong hơn "
                f"{max_age_days:.0f} ngày — kiểm tra log/cron!"
            )
    return stale


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    check_and_alert(["reddit_drama"])
    # Issue #72/#74/#75: job health 2 lần/ngày (06:30 + 18:30) soát service
    # launchd chưa load VÀ service loaded-nhưng-fail (tự re-bootstrap cái kẹt
    # EX_CONFIG). Chạy trước pipeline AI (07:00) nên có thể tự chữa để pipeline
    # kịp chạy đúng giờ. Best-effort — máy không phải macOS thì tự bỏ qua.
    try:
        from storage.launchd_status import check_and_alert as _launchd_check
        _launchd_check(self_label="com.ai5phut.drama-health")
    except Exception as e:
        logger.warning("Launchd status check failed (non-fatal): %s", e)
