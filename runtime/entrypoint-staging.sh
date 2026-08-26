#!/usr/bin/env bash
set -euo pipefail

STATE_DIR="${OPENCLAW_STATE_DIR:-/home/node/.openclaw}"

# This entrypoint runs as root, but supervisord starts openclaw and the agent
# server as `node`. Every directory root creates under the state dir stays
# root-owned, and openclaw_start dies on its very first
# `mkdir ~/.openclaw/agents/main` with EACCES -> 10 restarts -> FATAL, while the
# VM itself stays happily "started". So take ownership deterministically here,
# before anything writes into the state dir, rather than racing a background
# chown from supervisord.
#
# Recursive only for agents/ and credentials/: those are the paths root used to
# create (state restore below), and they are small. The bulk of the state dir
# (state/, extensions/, npm/ from the baked-in mem0 plugin — tens of thousands
# of files) is already node-owned from the image build, so crawling it on every
# boot would only cost I/O during startup.
mkdir -p "$STATE_DIR/agents" "$STATE_DIR/credentials"
chown node:node "$STATE_DIR"
chown -R node:node "$STATE_DIR/agents" "$STATE_DIR/credentials"

if [ "${RESET_STATE:-}" = "1" ]; then
  rm -rf "$STATE_DIR"/workspace-*/memory
  rm -f "$STATE_DIR"/workspace-*/MEMORY.md
  # Chats live in the per-agent SQLite store; the -wal/-shm sidecars hold pages that would
  # otherwise be replayed into a fresh database. The jsonl/lock sweep below is for machines
  # whose state predates the SQLite store, and for the archive the import leaves behind.
  rm -f "$STATE_DIR"/agents/*/agent/openclaw-agent.sqlite* 2>/dev/null || true
  rm -rf "$STATE_DIR"/agents/*/session-sqlite-import-archive
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

# Drop to `node` for the restore: it unpacks the backup into
# ~/.openclaw/agents/<id>/sessions/, and running it as root is what left those
# directories unwritable for openclaw.
cd /app && runuser -u node -- env HOME=/home/node python -m sellerclaw_agent.cloud.restore_state || true

exec supervisord -n -c /etc/supervisor/conf.d/openclaw.conf
