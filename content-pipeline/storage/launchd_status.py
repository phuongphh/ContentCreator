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
_RELOAD_TIMEOUT = 30    # giây — reload 1 service (bootout+bootstrap) qua install.sh

# exit 78 = EX_CONFIG (sysexits.h). launchd/xpcproxy trả code này khi KHÔNG dựng
# được job TRƯỚC khi exec binary — nguyên nhân #74/#75: xpcproxy chdir vào
# WorkingDirectory / mở StandardOutPath và giữ handle inode; khi thư mục bị xoá &
# tạo lại (rebuild venv, re-clone, reconfig) handle stale → 78 trước cả khi binary
# chạy, và 78 KHOÁ job (KeepAlive cũng không restart) tới khi reload. Sau khi các
# plist đã bỏ WorkingDirectory/StandardOutPath (wrapper lo runtime), 78 hiếm đi,
# nhưng self-heal vẫn giữ vì reload là cách DUY NHẤT gỡ job kẹt. Chỉ status NÀY mới
# được auto re-bootstrap: job kẹt 78 chắc chắn CHƯA chạy gì (spawn fail) nên
# bootout+bootstrap không cắt ngang việc đang làm dở.
EX_CONFIG = 78


def expected_services() -> list[str]:
    """Label các service kỳ vọng, suy từ tên file plist trong repo.

    Convention của launchd/: tên file == Label trong plist (install.sh dựa
    vào điều này), nên chỉ cần đọc tên file — không phải parse XML.
    """
    pattern = os.path.join(LAUNCHD_DIR, "com.ai5phut.*.plist")
    return sorted(os.path.splitext(os.path.basename(p))[0]
                  for p in glob.glob(pattern))


def service_statuses() -> Optional[dict[str, Optional[int]]]:
    """Map {label: last-exit-status} từ MỘT lần `launchctl list`.

    Output launchctl 3 cột: "PID\\tStatus\\tLabel". Cột giữa (Status) chính là
    exit status của lần chạy gần nhất — nên phát hiện service loaded-nhưng-FAIL
    (vd EX_CONFIG 78 của #74/#75) KHÔNG tốn thêm call nào so với loaded_services
    cũ. Status '-' hoặc không phải số → None (chưa chạy / đang chạy / signal).
    Trả None nếu không xác định được (không phải macOS / launchctl lỗi / timeout).
    """
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

    statuses: dict[str, Optional[int]] = {}
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        label = parts[-1].strip()
        if not label or label == "Label":  # bỏ dòng header
            continue
        try:
            statuses[label] = int(parts[1].strip())
        except ValueError:
            statuses[label] = None
    return statuses


def loaded_services() -> Optional[set[str]]:
    """Tập label đang được load trong launchd, hoặc None nếu không xác định
    được (không phải macOS / launchctl lỗi / timeout)."""
    statuses = service_statuses()
    return None if statuses is None else set(statuses)


def missing_services() -> Optional[list[str]]:
    """Service có plist trong repo nhưng CHƯA load; None nếu không xác định."""
    loaded = loaded_services()
    if loaded is None:
        return None
    return [name for name in expected_services() if name not in loaded]


def failing_services() -> Optional[dict[str, int]]:
    """Service đã load nhưng lần chạy gần nhất FAIL (exit != 0), map {label: code}.

    Đây là điểm mù của watchdog #72 (chỉ soát "chưa load"): #74/#75 là service
    ĐÃ load nhưng kẹt EX_CONFIG (78) tại giờ schedule → missing_services() rỗng,
    watchdog cũ im lặng. None nếu không xác định (không phải macOS...).
    """
    statuses = service_statuses()
    if statuses is None:
        return None
    expected = set(expected_services())
    return {label: code for label, code in statuses.items()
            if label in expected and code not in (None, 0)}


def reload_service(label: str) -> bool:
    """Re-bootstrap 1 service qua `launchd/install.sh reload <label>` (idempotent).

    Đây chính là thao tác "unload/load" mà issue #74 xác nhận là fix được
    EX_CONFIG — nay tự động hoá. Best-effort: trả False (không raise) nếu không
    chạy được (không phải macOS / timeout / script lỗi)."""
    script = os.path.join(LAUNCHD_DIR, "install.sh")
    try:
        proc = subprocess.run(
            ["/bin/bash", script, "reload", label],
            capture_output=True, text=True, timeout=_RELOAD_TIMEOUT,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        logger.warning("reload_service(%s) không chạy được (%s)", label, e)
        return False
    if proc.returncode != 0:
        logger.warning("reload_service(%s) exit %d: %s",
                       label, proc.returncode, proc.stderr.strip())
        return False
    logger.info("Đã re-bootstrap launchd service %s", label)
    return True


def _alert(msg: str) -> None:
    """Gửi Telegram best-effort — nuốt mọi lỗi (watchdog không được làm hỏng
    pipeline chính)."""
    try:
        from notifier.telegram_bot import send_alert
        send_alert(msg)
    except Exception as e:
        logger.warning("Launchd alert failed (non-fatal): %s", e)


def _check_missing_and_alert() -> list[str]:
    missing = missing_services()
    if not missing:  # None hoặc rỗng
        return []
    logger.warning("Launchd service có trong repo nhưng chưa được load: %s", missing)
    lines = "\n".join(f"  • {name}" for name in missing)
    _alert(
        f"⚠️ LAUNCHD: {len(missing)} service có plist trong repo nhưng "
        f"CHƯA được load:\n{lines}\n"
        f"Fix: chạy content-pipeline/launchd/install.sh trên Mac Mini "
        f"(idempotent — cài/refresh toàn bộ service)."
    )
    return missing


def _check_failing_and_heal(self_label: Optional[str], heal: bool) -> dict[str, int]:
    """Phát hiện service loaded-nhưng-fail; tự re-bootstrap cái kẹt EX_CONFIG.

    Bỏ qua `self_label` (service đang chạy chính hàm này): status của nó là lần
    chạy TRƯỚC, và bootout chính mình sẽ tự giết process giữa chừng. Chỉ auto
    reload status == EX_CONFIG (78) — spawn fail nên chắc chắn không cắt ngang
    việc dở; các fail khác chỉ alert (reload không chữa được bug runtime).
    """
    failing = failing_services()
    if not failing:  # None hoặc rỗng
        return {}
    failing = {l: c for l, c in failing.items() if l != self_label}
    if not failing:
        return {}

    logger.warning("Launchd service loaded nhưng fail: %s", failing)
    healed: list[str] = []
    still: list[str] = []
    for label, code in sorted(failing.items()):
        if heal and code == EX_CONFIG and reload_service(label):
            healed.append(label)
        else:
            still.append(f"{label} (exit {code})")

    parts = ["⚠️ LAUNCHD: service đã load nhưng lần chạy gần nhất FAIL."]
    if healed:
        parts.append("🔧 Đã tự re-bootstrap (EX_CONFIG 78 — job kẹt do stale "
                     "handle sau rebuild/re-clone, xem #74/#75):\n" +
                     "\n".join(f"  • {n}" for n in healed))
    if still:
        parts.append("❌ Vẫn fail — cần xem tay "
                     "(launchctl print gui/$(id -u)/<label>):\n" +
                     "\n".join(f"  • {n}" for n in still))
    _alert("\n".join(parts))
    return failing


def check_and_alert(self_label: Optional[str] = None, heal: bool = True) -> list[str]:
    """Watchdog launchd best-effort. Trả về danh sách service CHƯA load (giữ
    tương thích call site cũ).

    2 lớp phát hiện:
      1. service chưa load (issue #72) → alert kèm cách chạy install.sh.
      2. service đã load nhưng lần chạy gần nhất fail (issue #74/#75) → alert,
         và tự re-bootstrap cái kẹt EX_CONFIG (78). `self_label` là service
         đang chạy hàm này (được bỏ qua khi heal — tránh tự bootout mình).

    Không raise trong mọi trường hợp (kể cả Telegram lỗi) — chạy ké pipeline
    chính, không được làm hỏng pipeline.
    """
    missing = _check_missing_and_alert()
    try:
        _check_failing_and_heal(self_label, heal)
    except Exception as e:  # tuyệt đối không để watchdog làm hỏng pipeline
        logger.warning("Launchd failing-check lỗi (non-fatal): %s", e)
    return missing


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    stale = check_and_alert()
    if stale:
        print("Missing:", ", ".join(stale))
    else:
        print("OK — không phát hiện service thiếu (hoặc không phải macOS).")
