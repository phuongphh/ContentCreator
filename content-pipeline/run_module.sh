#!/bin/bash
# run_module.sh — Wrapper launchd DÙNG CHUNG: chạy bất kỳ entrypoint nào bên
# trong venv. Mọi launchd job (trừ pipeline/bot đã có run_pipeline.sh) nên trỏ
# ProgramArguments vào script NÀY thay vì thẳng venv/bin/python3.
#
# Root cause #74/#75 (EX_CONFIG / exit 78):
#   Khi plist trỏ ProgramArguments[0] thẳng vào venv/bin/python3, launchd cache
#   vnode của đúng file đó lúc load. Reconfig .env/token thường kéo theo rebuild
#   venv → venv/bin/python3 bị xóa & tạo lại (inode mới) → vnode cached của
#   launchd thành stale → tới giờ schedule, posix_spawn thất bại với EX_CONFIG
#   (78) và job KHÔNG chạy cho tới khi được reload thủ công.
#   Một wrapper TĨNH (file này không bao giờ bị venv rebuild đụng tới) giữ vnode
#   cached của launchd ổn định, còn venv được resolve LẠI ở runtime mỗi lần chạy
#   → rebuild venv không còn phá được scheduled run.
#
# Dùng: run_module.sh -m package.module [args...]   (hoặc run_module.sh script.py)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_PYTHON="$SCRIPT_DIR/venv/bin/python3"

if [ ! -x "$VENV_PYTHON" ]; then
    echo "ERROR: venv python không tồn tại/không chạy được tại $VENV_PYTHON" >&2
    echo "Fix: cd $SCRIPT_DIR && python3 -m venv venv && venv/bin/pip install -r requirements.txt" >&2
    # 78 = EX_CONFIG — báo cho log launchd biết đây là lỗi cấu hình/venv, không
    # phải lỗi runtime của chính module.
    exit 78
fi

exec "$VENV_PYTHON" "$@"
