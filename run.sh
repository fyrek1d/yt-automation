#!/usr/bin/env bash
# Run one pipeline cycle and log output.
set -euo pipefail

cd "$(dirname "$0")"

LOG_DIR="logs"
mkdir -p "$LOG_DIR"

STAMP="$(date +%Y%m%d_%H%M%S)"
echo "=== Cycle $STAMP ==="

python src/main.py "$@" | tee "$LOG_DIR/cycle_$STAMP.log"
