#!/usr/bin/env bash
# Start the pipeline dashboard (idempotent). Used by cron @reboot.
set -e
cd "$(dirname "$0")"
mkdir -p logs
if pgrep -f "src/dashboard.py" > /dev/null 2>&1; then
  echo "dashboard already running"
  exit 0
fi
nohup .venv/bin/python src/dashboard.py --host 0.0.0.0 --port 8080 \
  >> logs/dashboard.log 2>&1 &
echo "dashboard started on :8080 (token in config/dashboard_secret.txt)"
