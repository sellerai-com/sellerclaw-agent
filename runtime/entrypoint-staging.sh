#!/usr/bin/env bash
set -euo pipefail

STATE_DIR="${OPENCLAW_STATE_DIR:-/home/node/.openclaw}"

if [ "${RESET_STATE:-}" = "1" ]; then
  rm -rf "$STATE_DIR"/workspace-*/memory
  rm -f "$STATE_DIR"/workspace-*/MEMORY.md
  find "$STATE_DIR"/agents -type f \( -name "*.jsonl" -o -name "*.lock" \) -path "*/sessions/*" -delete 2>/dev/null || true
fi

cd /app && python -m sellerclaw_agent.cloud.restore_state || true

exec supervisord -n -c /etc/supervisor/conf.d/openclaw.conf
