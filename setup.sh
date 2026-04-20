#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Parse --env <profile> argument and forward remaining args to the CLI.
CLI_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --env)
      export AGENT_ENV="${2:?--env requires a value (local, staging, production)}"
      shift 2
      ;;
    *)
      CLI_ARGS+=("$1")
      shift
      ;;
  esac
done

# Resolve the env file: .env.<profile> if AGENT_ENV is set, otherwise .env
ENV_FILE=".env"
if [[ -n "${AGENT_ENV:-}" ]]; then
  ENV_FILE=".env.${AGENT_ENV}"
  if [[ ! -f "$ENV_FILE" ]]; then
    echo "Error: env file '$ENV_FILE' not found." >&2
    exit 1
  fi
fi

# Export variables so both docker compose and the CLI see them.
set -a
# shellcheck source=/dev/null
source "$ENV_FILE"
set +a

if ! command -v docker &>/dev/null; then
  echo "Error: Docker is not installed." >&2
  exit 1
fi

if ! docker compose version &>/dev/null; then
  echo "Error: Docker Compose v2 is required (docker compose)." >&2
  exit 1
fi

if command -v uv &>/dev/null; then
  exec uv run sellerclaw-agent "${CLI_ARGS[@]:-setup}"
fi

if command -v pip &>/dev/null; then
  echo "uv not found, falling back to pip..."
  pip install -e . -q
  exec sellerclaw-agent "${CLI_ARGS[@]:-setup}"
fi

echo "Error: neither uv nor pip found. Install uv: https://docs.astral.sh/uv/" >&2
exit 1
