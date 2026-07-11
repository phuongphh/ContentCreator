#!/bin/bash
# run_module.sh — Wrapper launchd DÙNG CHUNG: chạy bất kỳ entrypoint nào bên
# trong venv. Mọi launchd job trỏ ProgramArguments vào script NÀY thay vì thẳng
# venv/bin/python3, VÀ plist KHÔNG khai báo WorkingDirectory/StandardOutPath/
# StandardErrorPath — wrapper tự thiết lập cwd + log ở runtime.
#
# Root cause #74/#75 (EX_CONFIG / exit 78) — đã kiểm chứng:
#   launchd/xpcproxy dựng WorkingDirectory + StandardOutPath/StandardErrorPath
#   TRƯỚC khi exec binary. Nếu các thư mục đó bị xoá & tạo lại (rebuild venv,
#   re-clone repo, reconfig) thì launchd giữ handle inode CŨ đã stale → xpcproxy
#   setup fail và trả EX_CONFIG (78) *trước khi binary chạy* (foreground chạy tốt,
#   log file không hề được tạo). Tệ hơn: exit 78 KHOÁ job vào trạng thái "spawn
#   scheduled" — KeepAlive cũng không restart — cho tới khi job được reload.
#
#   Vì thế plist chỉ còn trỏ vào MỘT path string duy nhất: wrapper này (launchd
#   re-resolve path mỗi lần spawn, không giữ handle thư mục). Wrapper thiết lập
#   LẠI cwd + log ở runtime với inode tươi → rebuild venv / re-clone không còn
#   phá được scheduled run.
#
# Dùng: run_module.sh -m package.module [args...]   (hoặc run_module.sh script.py)
# Env:  LOG_BASENAME — tên file log (mặc định "run_module"); log ghi vào
#       <script_dir>/logs/${LOG_BASENAME}_stdout.log + _stderr.log.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# --- Redirect log ở runtime (thay cho StandardOutPath/StandardErrorPath) ---
# Wrapper tự mở file log với inode tươi mỗi lần chạy → không còn stale-handle như
# khi để launchd/xpcproxy mở. Đặt SỚM để cả lỗi cấp wrapper (vd thiếu venv) cũng
# được ghi lại thay vì rơi vào hư không.
LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"
LOG_BASENAME="${LOG_BASENAME:-run_module}"
exec >>"$LOG_DIR/${LOG_BASENAME}_stdout.log" 2>>"$LOG_DIR/${LOG_BASENAME}_stderr.log"

# --- cwd = package root ở runtime (thay cho WorkingDirectory) ---
cd "$SCRIPT_DIR"

VENV_PYTHON="$SCRIPT_DIR/venv/bin/python3"
if [ ! -x "$VENV_PYTHON" ]; then
    echo "ERROR: venv python không tồn tại/không chạy được tại $VENV_PYTHON" >&2
    echo "Fix: cd $SCRIPT_DIR && python3 -m venv venv && venv/bin/pip install -r requirements.txt" >&2
    # 78 = EX_CONFIG — báo cho log launchd biết đây là lỗi cấu hình/venv.
    exit 78
fi

exec "$VENV_PYTHON" "$@"
