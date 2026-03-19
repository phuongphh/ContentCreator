#!/bin/bash
set -e

# AI 5 Phút Mỗi Ngày — Service Setup Script
# Auto-detects macOS (launchd) vs Linux (systemd)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_BIN="$(which python3)"

echo "=== AI 5 Phút Mỗi Ngày — Service Setup ==="
echo "Project dir: $SCRIPT_DIR"
echo "Python:      $PYTHON_BIN"
echo ""

# Check .env exists
if [ ! -f "$SCRIPT_DIR/.env" ]; then
    echo "ERROR: .env file not found at $SCRIPT_DIR/.env"
    echo "Copy .env.example and fill in your API keys first."
    exit 1
fi

if [[ "$OSTYPE" == "darwin"* ]]; then
    echo "Detected: macOS — using launchd"
    echo ""

    PLIST_DIR="$SCRIPT_DIR/launchd"
    DEST_DIR="$HOME/Library/LaunchAgents"
    mkdir -p "$DEST_DIR"

    # Update paths in plist files
    for plist in "$PLIST_DIR"/com.ai5phut.*.plist; do
        filename="$(basename "$plist")"
        echo "Installing $filename..."

        # Replace placeholder paths with actual paths
        sed "s|/Users/YOU/ContentCreator/content-pipeline|$SCRIPT_DIR|g; s|/usr/bin/python3|$PYTHON_BIN|g" \
            "$plist" > "$DEST_DIR/$filename"

        # Unload if already loaded, then load
        launchctl unload "$DEST_DIR/$filename" 2>/dev/null || true
        launchctl load "$DEST_DIR/$filename"
        echo "  → Loaded"
    done

    echo ""
    echo "Done! Check status:"
    echo "  launchctl list | grep ai5phut"

elif command -v systemctl &>/dev/null; then
    echo "Detected: Linux — using systemd"
    echo ""

    SYSTEMD_DIR="$SCRIPT_DIR/systemd"

    # Update paths in service files
    for unit in "$SYSTEMD_DIR"/ai5phut-*.{service,timer}; do
        [ -f "$unit" ] || continue
        filename="$(basename "$unit")"
        echo "Installing $filename..."

        # Replace paths with actual values
        sudo sed "s|/home/user/ContentCreator/content-pipeline|$SCRIPT_DIR|g; s|/usr/local/bin/python3|$PYTHON_BIN|g" \
            "$unit" > /tmp/"$filename"
        sudo mv /tmp/"$filename" /etc/systemd/system/"$filename"
        echo "  → Copied to /etc/systemd/system/"
    done

    sudo systemctl daemon-reload

    # Enable and start
    echo ""
    echo "Enabling services..."

    sudo systemctl enable --now ai5phut-pipeline.timer
    echo "  → Pipeline timer enabled (7:00 daily)"

    sudo systemctl enable --now ai5phut-bot.service
    echo "  → Telegram bot started"

    echo ""
    echo "Done! Check status:"
    echo "  systemctl list-timers | grep ai5phut"
    echo "  systemctl status ai5phut-bot"

else
    echo "ERROR: Neither launchd (macOS) nor systemd (Linux) found."
    echo "Please set up scheduling manually (crontab, etc.)"
    exit 1
fi
