#!/usr/bin/env bash
# SellerClaw host entrypoint.
#
# Usage:
#   ./setup.sh                       — full install + connect wizard (default env: production)
#   ./setup.sh --env local           — switch environment profile
#   ./setup.sh stop                  — stop the running container
#   ./setup.sh start                 — start the container
#   ./setup.sh status                — show connection status
#   ./setup.sh login                 — connect to your account
#   ./setup.sh logout                — disconnect from your account
#   ./setup.sh help                  — show CLI help
#
# The full installer (no subcommand / 'setup') runs system checks and
# installs missing host dependencies (docker, docker compose v2, uv).
# Every other subcommand expects these to be present and just forwards to the CLI.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

MIN_RAM_MB=2048
AGENT_ENV="${AGENT_ENV:-production}"
ASSUME_YES=0
CLI_ARGS=()

log_step()  { printf '\n\033[1;36m==>\033[0m %s\n' "$*"; }
log_ok()    { printf '    \033[0;32m✓\033[0m %s\n' "$*"; }
log_warn()  { printf '    \033[0;33m!\033[0m %s\n' "$*"; }
log_err()   { printf '    \033[0;31m✗\033[0m %s\n' "$*" >&2; }

die() { log_err "$*"; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env)
      [[ $# -ge 2 ]] || die "--env requires a value (local, staging, production)"
      AGENT_ENV="$2"
      shift 2
      ;;
    --env=*)
      AGENT_ENV="${1#--env=}"
      shift
      ;;
    -y|--yes)
      ASSUME_YES=1
      shift
      ;;
    -h|--help)
      cat <<EOF
Usage: ./setup.sh [--env local|staging|production] [-y|--yes] [command]

Commands:
  (none)  setup   Install SellerClaw on this host and connect to your account.
  start           Start the SellerClaw container.
  stop            Stop the SellerClaw container.
  status          Show connection status.
  login           Connect to your account.
  logout          Disconnect from your account.
  help            Show CLI help.

Options:
  --env <profile>   Environment profile: local, staging, production (default: production).
  -y, --yes         Assume "yes" for privileged installs of missing host dependencies.
  -h, --help        Show this message.
EOF
      exit 0
      ;;
    *)
      CLI_ARGS+=("$1")
      shift
      ;;
  esac
done

case "$AGENT_ENV" in
  local|staging|production) ;;
  *) die "Invalid --env '$AGENT_ENV' (expected: local, staging, production)" ;;
esac
export AGENT_ENV

# ---------------------------------------------------------------------------
# Decide what this invocation actually needs.
# ---------------------------------------------------------------------------

SUBCMD="${CLI_ARGS[0]:-setup}"

NEED_INSTALLER=0   # run RAM check + auto-install missing docker/uv
NEED_ENV_FILE=0    # read .env.<profile> before launching the CLI
NEED_DOCKER=0      # verify docker + docker compose v2 are present
NEED_UV=1          # CLI is always launched via `uv run`

case "$SUBCMD" in
  setup)
    NEED_INSTALLER=1
    NEED_ENV_FILE=1
    NEED_DOCKER=1
    ;;
  start|stop)
    NEED_ENV_FILE=1
    NEED_DOCKER=1
    ;;
  status|login|logout)
    ;;
  help|"")
    ;;
  *)
    # Unknown subcommand — forward as-is; the CLI will error out with its help.
    ;;
esac

ENV_FILE=".env.${AGENT_ENV}"
if (( NEED_ENV_FILE )); then
  [[ -f "$ENV_FILE" ]] || die "Environment file '$ENV_FILE' not found."
fi

have_cmd() { command -v "$1" &>/dev/null; }

# ---------------------------------------------------------------------------
# Installer-only helpers.
# ---------------------------------------------------------------------------

OS_NAME="$(uname -s)"

sudo_cmd() {
  if [[ $EUID -eq 0 ]]; then
    "$@"
  elif have_cmd sudo; then
    sudo "$@"
  else
    die "This step needs root (sudo not found). Re-run as root: sudo $*"
  fi
}

confirm_install() {
  local what="$1"
  if [[ $ASSUME_YES -eq 1 ]]; then
    return 0
  fi
  if [[ ! -t 0 ]]; then
    die "Missing dependency: $what. Re-run with --yes to auto-install, or install it manually."
  fi
  read -r -p "    Install $what now? [Y/n] " reply
  case "${reply:-Y}" in
    Y|y|"") return 0 ;;
    *) die "Aborted: $what is required." ;;
  esac
}

DISTRO_ID=""
DISTRO_LIKE=""
if [[ -r /etc/os-release ]]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  DISTRO_ID="${ID:-}"
  DISTRO_LIKE="${ID_LIKE:-}"
fi

apt_update_done=0
pkg_install() {
  case "$DISTRO_ID" in
    ubuntu|debian)
      if (( apt_update_done == 0 )); then
        sudo_cmd apt-get update -y
        apt_update_done=1
      fi
      sudo_cmd env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "$@"
      ;;
    fedora)               sudo_cmd dnf install -y "$@" ;;
    centos|rhel|rocky|almalinux) sudo_cmd yum install -y "$@" ;;
    arch|manjaro|endeavouros)    sudo_cmd pacman -Sy --noconfirm "$@" ;;
    *)
      case "$DISTRO_LIKE" in
        *debian*|*ubuntu*)
          if (( apt_update_done == 0 )); then
            sudo_cmd apt-get update -y
            apt_update_done=1
          fi
          sudo_cmd env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "$@"
          ;;
        *rhel*|*fedora*) sudo_cmd dnf install -y "$@" ;;
        *arch*)          sudo_cmd pacman -Sy --noconfirm "$@" ;;
        *) die "Unsupported distro '$DISTRO_ID'; install these packages manually: $*" ;;
      esac
      ;;
  esac
}

install_docker_via_official_script() {
  log_warn "Installing Docker Engine via https://get.docker.com"
  have_cmd curl || pkg_install curl ca-certificates
  tmp="$(mktemp)"
  curl -fsSL https://get.docker.com -o "$tmp"
  sudo_cmd sh "$tmp"
  rm -f "$tmp"
}

ensure_docker_installed() {
  if have_cmd docker; then
    return 0
  fi
  if (( NEED_INSTALLER )); then
    confirm_install "Docker Engine"
    install_docker_via_official_script
    have_cmd docker || die "Docker installation failed."
  else
    die "Docker is not installed. Run ./setup.sh first to install it."
  fi
}

ensure_docker_compose_installed() {
  if docker compose version &>/dev/null; then
    return 0
  fi
  if (( NEED_INSTALLER )); then
    confirm_install "Docker Compose v2 plugin"
    case "$DISTRO_ID" in
      arch|manjaro|endeavouros) pkg_install docker-compose ;;
      *)                         pkg_install docker-compose-plugin ;;
    esac
    docker compose version &>/dev/null || die "Docker Compose v2 installation failed."
  else
    die "Docker Compose v2 is not installed. Run ./setup.sh first."
  fi
}

ensure_docker_daemon_running() {
  if docker info &>/dev/null; then
    return 0
  fi
  if (( NEED_INSTALLER )) && have_cmd systemctl; then
    log_warn "Starting docker service (systemctl)"
    sudo_cmd systemctl enable --now docker || true
  fi
  docker info &>/dev/null || die "Docker daemon is not reachable. Try: sudo systemctl start docker"
}

ensure_uv_installed() {
  if have_cmd uv; then
    return 0
  fi
  if (( NEED_INSTALLER )); then
    confirm_install "uv (Python package manager)"
    have_cmd curl || pkg_install curl ca-certificates
    curl -LsSf https://astral.sh/uv/install.sh | sh
    for cand in "$HOME/.local/bin" "$HOME/.cargo/bin"; do
      if [[ -x "$cand/uv" ]]; then
        export PATH="$cand:$PATH"
        break
      fi
    done
    have_cmd uv || die "uv installation failed. Restart your shell or add ~/.local/bin to PATH."
  else
    die "uv is not installed. Run ./setup.sh first."
  fi
}

# ---------------------------------------------------------------------------
# Hardware / dependency checks (only relevant for the full installer).
# ---------------------------------------------------------------------------

if (( NEED_INSTALLER )); then
  if [[ "$OS_NAME" != "Linux" ]]; then
    die "This installer supports Linux only (detected '$OS_NAME'). Install docker, docker compose v2, and uv manually."
  fi

  log_step "Checking hardware"
  if [[ -r /proc/meminfo ]]; then
    MEM_KB="$(awk '/^MemTotal:/ {print $2; exit}' /proc/meminfo || echo 0)"
    MEM_MB=$(( MEM_KB / 1024 ))
    if (( MEM_MB <= MIN_RAM_MB )); then
      die "Not enough RAM: detected ${MEM_MB} MB, SellerClaw requires more than ${MIN_RAM_MB} MB."
    fi
    log_ok "RAM: ${MEM_MB} MB (> ${MIN_RAM_MB} MB required)"
  else
    log_warn "Cannot read /proc/meminfo; skipping RAM check."
  fi

  log_step "Checking Docker"
  ensure_docker_installed
  log_ok "docker: $(docker --version 2>/dev/null || echo ok)"
  ensure_docker_compose_installed
  log_ok "docker compose: $(docker compose version 2>/dev/null | head -n1)"
  ensure_docker_daemon_running
  log_ok "docker daemon is running"

  log_step "Checking uv"
  ensure_uv_installed
  log_ok "uv: $(uv --version 2>/dev/null || echo ok)"
else
  # Quick validation: just make sure what we need exists, error with a clear message otherwise.
  if (( NEED_DOCKER )); then
    ensure_docker_installed
    ensure_docker_compose_installed
    ensure_docker_daemon_running
  fi
  if (( NEED_UV )); then
    ensure_uv_installed
  fi
fi

# ---------------------------------------------------------------------------
# Load environment profile when the subcommand needs it.
# ---------------------------------------------------------------------------

if (( NEED_ENV_FILE )); then
  set -a
  # shellcheck source=/dev/null
  source "$ENV_FILE"
  set +a
fi

# ---------------------------------------------------------------------------
# Hand off to the CLI.
# ---------------------------------------------------------------------------

exec uv run --quiet sellerclaw-agent "${CLI_ARGS[@]:-setup}"
