from __future__ import annotations

"""
Launchd service status — watchdog cho issue #72.

Root cause của #72: plist mới được thêm vào repo mỗi phase (5, 6...) nhưng cài
đặt launchd là quy trình thủ công — không gì phát hiện "service có trong repo
nhưng chưa được load", nên drama-pipeline/post-scheduler/metrics-pull nằm im
hàng tuần. Watchdog sẵn có (storage/collector_health.py) không bắt được vì
chính nó cũng là một service chưa load (chicken-and-egg).

Module này phá vòng chicken-and-egg bằng cách để service ĐANG CHẠY tự kiểm tra
các service còn lại: `check_and_alert()` được gọi từ main.py (pipeline AI 07:00
— service được xác nhận đang chạy) và từ job drama-health. Danh sách service
kỳ vọng suy trực tiếp từ `launchd/com.ai5phut.*.plist` trong repo (tên file ==
Label — xem launchd/install.sh), nên plist mới thêm tự động được theo dõi,
không cần cập nhật danh sách ở đâu khác.

Trên máy không phải macOS (CI, dev Linux) launchctl không tồn tại →
mọi hàm trả "không xác định" và không alert gì (non-fatal, không chậm pipeline:
đúng 1 lần gọi `launchctl list` có timeout).
"""

import glob
import logging
import os
import subprocess
from typing import Optional

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logger = logging.getLogger(__name__)

LAUNCHD_DIR = os.path.join(os.path.dirname(__file__), "..", "launchd")
_LAUNCHCTL_TIMEOUT = 5  # giây — check trạng thái không được phép chậm pipeline


def expected_services() -> list[str]:
    """Label các service kỳ vọng, suy từ tên file plist trong repo.

    Convention của launchd/: tên file == Label trong plist (install.sh dựa
    vào điều này), nên chỉ cần đọc tên file — không phải parse XML.
    """
    pattern = os.path.join(LAUNCHD_DIR, "com.ai5phut.*.plist")
    return sorted(os.path.splitext(os.path.basename(p))[0]
                  for p in glob.glob(pattern))


def loaded_services() -> Optional[set[str]]:
    """Tập label đang được load trong launchd, hoặc None nếu không xác định
    được (không phải macOS / launchctl lỗi / timeout)."""
    try:
        proc = subprocess.run(
            ["launchctl", "list"],
            capture_output=True, text=True, timeout=_LAUNCHCTL_TIMEOUT,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        logger.debug("launchctl không khả dụng (%s) — bỏ qua launchd check", e)
        return None
    if proc.returncode != 0:
        logger.debug("launchctl list exit %d — bỏ qua launchd check", proc.returncode)
        return None

    # Output: "PID\tStatus\tLabel" (dòng đầu là header) — lấy cột cuối.
    labels = set()
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if parts:
            labels.add(parts[-1].strip())
    return labels


def missing_services() -> Optional[list[str]]:
    """Service có plist trong repo nhưng CHƯA load; None nếu không xác định."""
    loaded = loaded_services()
    if loaded is None:
        return None
    return [name for name in expected_services() if name not in loaded]


def check_and_alert() -> list[str]:
    """Alert Telegram nếu có service chưa load. Trả về danh sách missing.

    Không raise trong mọi trường hợp (kể cả Telegram lỗi) — đây là watchdog
    best-effort chạy ké trong pipeline chính, không được làm hỏng pipeline.
    """
    missing = missing_services()
    if missing is None or not missing:
        return []

    logger.warning("Launchd service có trong repo nhưng chưa được load: %s", missing)
    try:
        from notifier.telegram_bot import send_alert
        lines = "\n".join(f"  • {name}" for name in missing)
        send_alert(
            f"⚠️ LAUNCHD: {len(missing)} service có plist trong repo nhưng "
            f"CHƯA được load:\n{lines}\n"
            f"Fix: chạy content-pipeline/launchd/install.sh trên Mac Mini "
            f"(idempotent — cài/refresh toàn bộ service)."
        )
    except Exception as e:
        logger.warning("Launchd alert failed (non-fatal): %s", e)
    return missing


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    stale = check_and_alert()
    if stale:
        print("Missing:", ", ".join(stale))
    else:
        print("OK — không phát hiện service thiếu (hoặc không phải macOS).")
