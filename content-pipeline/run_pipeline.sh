#!/bin/bash
# Wrapper script for launchd — ensures pipeline always runs inside the venv.
# The root cause of missing deps (Pillow, googleapiclient) was that launchd
# called /opt/homebrew/bin/python3 directly, which is a different Python than
# where packages were installed. This script fixes that permanently.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_PYTHON="$SCRIPT_DIR/venv/bin/python3"

if [ ! -f "$VENV_PYTHON" ]; then
    echo "ERROR: venv not found at $SCRIPT_DIR/venv" >&2
    echo "Run: cd $SCRIPT_DIR && /opt/homebrew/bin/python3 -m venv venv && venv/bin/pip install -r requirements.txt" >&2
    exit 1
fi

exec "$VENV_PYTHON" "$SCRIPT_DIR/main.py" "$@"
