#!/bin/bash
# install.sh — Cài/refresh TOÀN BỘ launchd service của content-pipeline (issue #72).
#
# Root cause #72: cài đặt trước đây là quy trình thủ công 4 bước (sửa placeholder,
# copy từng file, load từng file) nên plist mới thêm ở Phase 5/6 chưa từng được
# load. Script này thay quy trình đó bằng 1 lệnh idempotent:
#
#   ./install.sh            # cài/refresh mọi com.ai5phut.*.plist trong thư mục này
#   ./install.sh status     # xem service nào đã load / còn thiếu
#   ./install.sh reload [L] # re-bootstrap toàn bộ (hoặc 1 label) — dùng khi 1
#                           #   service kẹt EX_CONFIG do stale handle sau rebuild
#                           #   venv/re-clone (issue #74/#75). storage/launchd_
#                           #   status.py gọi `reload <label>` để self-heal.
#   ./install.sh uninstall  # gỡ toàn bộ service
#
# Nguyên tắc:
# - Glob toàn bộ com.ai5phut.*.plist — plist mới thêm vào repo tự động được cài,
#   không cần cập nhật script/README (chính lỗ hổng gây ra #72).
# - Render placeholder /Users/YOU/... sang đường dẫn thật khi copy vào
#   ~/Library/LaunchAgents — KHÔNG sửa file trong repo (không dirty git).
# - Idempotent: chạy lại bao nhiêu lần cũng được; service đang load được
#   bootout rồi bootstrap lại (bot có RunAtLoad nên tự chạy lại ngay).
# - Convention: tên file plist == Label bên trong (storage/launchd_status.py
#   và script này đều dựa vào điều đó).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"     # .../content-pipeline/launchd
PIPELINE_DIR="$(dirname "$SCRIPT_DIR")"          # .../content-pipeline
LA_DIR="$HOME/Library/LaunchAgents"
PLACEHOLDER="/Users/YOU/ContentCreator/content-pipeline"
GUI_DOMAIN="gui/$(id -u)"
CMD="${1:-install}"

require_macos() {
    if [ "$(uname)" != "Darwin" ] || ! command -v launchctl >/dev/null 2>&1; then
        echo "ERROR: launchd chỉ có trên macOS — chạy script này trên Mac Mini." >&2
        exit 1
    fi
}

is_loaded() {
    launchctl list "$1" >/dev/null 2>&1
}

# Render placeholder → đường dẫn thật rồi bootout+bootstrap 1 plist. Trả về
# trạng thái is_loaded. Dùng chung bởi do_install và do_reload (idempotent).
bootstrap_one() {
    local src="$1" label dst
    label="$(basename "$src" .plist)"
    dst="$LA_DIR/$label.plist"

    # Render placeholder → đường dẫn thật (repo giữ nguyên placeholder)
    sed "s|$PLACEHOLDER|$PIPELINE_DIR|g" "$src" > "$dst"

    # Idempotent: gỡ bản đang load (nếu có) rồi load bản vừa render. Đây chính
    # là thao tác "reload" khắc phục EX_CONFIG khi launchd giữ handle inode stale
    # (WorkingDirectory/StandardOutPath) sau rebuild venv/re-clone (issue #74/#75).
    launchctl bootout "$GUI_DOMAIN/$label" >/dev/null 2>&1 || true
    if ! launchctl bootstrap "$GUI_DOMAIN" "$dst" 2>/dev/null; then
        # Fallback cho macOS cũ chưa có bootstrap
        launchctl load -w "$dst" 2>/dev/null || true
    fi

    is_loaded "$label"
}

warn_if_no_venv() {
    if [ ! -x "$PIPELINE_DIR/venv/bin/python3" ]; then
        echo "WARNING: venv chưa có tại $PIPELINE_DIR/venv — service sẽ fail khi chạy." >&2
        echo "         Fix: cd $PIPELINE_DIR && python3 -m venv venv && venv/bin/pip install -r requirements.txt" >&2
    fi
    # Wrapper tĩnh phải executable — plist trỏ vào đây (issue #74/#75).
    for w in run_module.sh run_pipeline.sh; do
        if [ ! -x "$PIPELINE_DIR/$w" ]; then
            echo "WARNING: $PIPELINE_DIR/$w không có execute bit — chmod +x giúp launchd chạy được." >&2
        fi
    done
}

do_install() {
    require_macos
    mkdir -p "$LA_DIR" "$PIPELINE_DIR/logs"
    warn_if_no_venv

    local failed=0
    for src in "$SCRIPT_DIR"/com.ai5phut.*.plist; do
        local label
        label="$(basename "$src" .plist)"
        if bootstrap_one "$src"; then
            echo "  ✅ $label"
        else
            echo "  ❌ $label — KHÔNG load được. Xem: launchctl print $GUI_DOMAIN/$label" >&2
            failed=1
        fi
    done

    if [ "$failed" -eq 0 ]; then
        echo "Hoàn tất — mọi service đã load. Kiểm tra lại bất kỳ lúc nào: $0 status"
    else
        echo "Có service load thất bại — xem thông báo phía trên." >&2
        exit 1
    fi
}

# Re-bootstrap toàn bộ (không tham số) hoặc đúng 1 label. Dùng để chữa service
# kẹt EX_CONFIG (78) mà không đụng các service còn lại — storage/launchd_status
# gọi `reload <label>` để self-heal, cố ý KHÔNG reload chính service đang chạy
# nó (tránh tự bootout mình giữa chừng).
do_reload() {
    require_macos
    mkdir -p "$LA_DIR" "$PIPELINE_DIR/logs"
    local target="${1:-}"

    if [ -n "$target" ]; then
        local src="$SCRIPT_DIR/$target.plist"
        if [ ! -f "$src" ]; then
            echo "ERROR: không tìm thấy plist cho '$target' trong $SCRIPT_DIR" >&2
            exit 1
        fi
        if bootstrap_one "$src"; then
            echo "  🔄 $target"
        else
            echo "  ❌ $target — reload thất bại. Xem: launchctl print $GUI_DOMAIN/$target" >&2
            exit 1
        fi
    else
        warn_if_no_venv
        local failed=0
        for src in "$SCRIPT_DIR"/com.ai5phut.*.plist; do
            local label
            label="$(basename "$src" .plist)"
            if bootstrap_one "$src"; then
                echo "  🔄 $label"
            else
                echo "  ❌ $label" >&2
                failed=1
            fi
        done
        [ "$failed" -eq 0 ] || exit 1
    fi
}

do_status() {
    require_macos
    local missing=0
    for src in "$SCRIPT_DIR"/com.ai5phut.*.plist; do
        local label
        label="$(basename "$src" .plist)"
        if is_loaded "$label"; then
            echo "  ✅ loaded   $label"
        else
            echo "  ❌ MISSING  $label"
            missing=1
        fi
    done
    if [ "$missing" -ne 0 ]; then
        echo "Có service chưa load — chạy: $0" >&2
        exit 1
    fi
}

do_uninstall() {
    require_macos
    for src in "$SCRIPT_DIR"/com.ai5phut.*.plist; do
        local label
        label="$(basename "$src" .plist)"
        launchctl bootout "$GUI_DOMAIN/$label" >/dev/null 2>&1 || true
        rm -f "$LA_DIR/$label.plist"
        echo "  🗑  $label"
    done
    echo "Đã gỡ toàn bộ service."
}

case "$CMD" in
    install)   do_install ;;
    status)    do_status ;;
    reload)    do_reload "${2:-}" ;;
    uninstall) do_uninstall ;;
    *)
        echo "Usage: $0 [install|status|reload [label]|uninstall]" >&2
        exit 2
        ;;
esac
