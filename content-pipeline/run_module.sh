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
# Env:  LOG_BASENAME  — tên file log (mặc định "run_module"); log ghi vào
#       <script_dir>/logs/${LOG_BASENAME}_stdout.log + _stderr.log.
#       LOG_DIR       — thư mục log (mặc định <script_dir>/logs).
#       LOG_MAX_BYTES — trần size mỗi file log (mặc định 50MB, issue #107).

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# --- Redirect log ở runtime (thay cho StandardOutPath/StandardErrorPath) ---
# Wrapper tự mở file log với inode tươi mỗi lần chạy → không còn stale-handle như
# khi để launchd/xpcproxy mở. Đặt SỚM để cả lỗi cấp wrapper (vd thiếu venv) cũng
# được ghi lại thay vì rơi vào hư không.
LOG_DIR="${LOG_DIR:-$SCRIPT_DIR/logs}"
mkdir -p "$LOG_DIR"
LOG_BASENAME="${LOG_BASENAME:-run_module}"

# --- Log rotation (issue #107: bot_stdout.log phình 3.4GB vì chỉ append) ---
# Rotate lúc SPAWN (trước redirect): file vượt trần → giữ ĐUÔI mới nhất
# (LOG_MAX_BYTES) sang <file>.1 rồi làm rỗng file chính. tail -c thay vì mv
# nguyên file để một file đã phình khổng lồ (3.4GB) co ngay về trần trong một
# lần rotate — không cần dọn tay. Tối đa ~2×LOG_MAX_BYTES/stream trên đĩa.
LOG_MAX_BYTES="${LOG_MAX_BYTES:-52428800}"
rotate_log() {
    local f="$1" size
    [ -f "$f" ] || return 0
    size=$(wc -c < "$f" 2>/dev/null | tr -d '[:space:]') || size=0
    [ -n "$size" ] || size=0
    if [ "$size" -gt "$LOG_MAX_BYTES" ]; then
        tail -c "$LOG_MAX_BYTES" "$f" > "$f.1" 2>/dev/null || cp "$f" "$f.1"
        : > "$f"
    fi
}
rotate_log "$LOG_DIR/${LOG_BASENAME}_stdout.log"
rotate_log "$LOG_DIR/${LOG_BASENAME}_stderr.log"

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
