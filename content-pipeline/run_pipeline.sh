#!/bin/bash
# Wrapper script for launchd — ensures the pipeline always runs inside the venv.
# The root cause of missing deps (Pillow, googleapiclient) was that launchd
# called /opt/homebrew/bin/python3 directly, which is a different Python than
# where packages were installed. This script fixes that permanently.
#
# Việc resolve venv được uỷ cho run_module.sh (single source of truth, cũng là
# wrapper tĩnh dùng chung cho mọi job khác — xem run_module.sh về root cause
# EX_CONFIG/#74/#75). run_pipeline.sh chỉ cố định entrypoint = main.py.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

exec "$SCRIPT_DIR/run_module.sh" "$SCRIPT_DIR/main.py" "$@"
