#!/usr/bin/env bash
set -euo pipefail

STATE_DIR="${OPENCLAW_STATE_DIR:-/home/node/.openclaw}"

if [ "${RESET_STATE:-}" = "1" ]; then
  rm -rf "$STATE_DIR"/workspace-*/memory
  rm -f "$STATE_DIR"/workspace-*/MEMORY.md
  find "$STATE_DIR"/agents -type f \( -name "*.jsonl" -o -name "*.lock" \) -path "*/sessions/*" -delete 2>/dev/null || true
fi

# Developer-only: if compose mounted a real sellerclaw-cli checkout at
# /opt/sellerclaw-cli-src (because SELLERCLAW_CLI_LOCAL_PATH was set in dev.env),
# install it editable system-wide so the `sellerclaw` binary reflects the host
# source on every container start — no image rebuild required. When the
# placeholder (./runtime/.no-local-cli) is mounted instead, pyproject.toml is
# absent and this block is a no-op, falling back to the CLI baked into the image.
if [ -f /opt/sellerclaw-cli-src/pyproject.toml ]; then
  echo "entrypoint: detected local sellerclaw-cli checkout — installing editable"
  UV_BREAK_SYSTEM_PACKAGES=1 uv pip install --system --no-cache \
    --reinstall-package sellerclaw-cli \
    -e /opt/sellerclaw-cli-src || \
    echo "entrypoint: local sellerclaw-cli install failed; continuing with baked-in version" >&2
fi

cd /app && python -m sellerclaw_agent.cloud.restore_state || true

exec supervisord -n -c /etc/supervisor/conf.d/openclaw.conf
