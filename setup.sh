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
# The full installer (no subcommand / 'setup') runs system checks and installs
# missing host dependencies where practical (Linux: docker/compose/uv; macOS:
# Docker Desktop via Homebrew when available, uv).
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

NEED_INSTALLER=0    # run RAM check + auto-install missing docker/uv
NEED_ENV_FILE=0     # read .env.<profile> before launching the CLI
NEED_DOCKER=0       # verify docker + docker compose v2 are present
NEED_UV=1           # CLI is always launched via `uv run`
NEED_CLI_UPGRADE=0  # re-resolve sellerclaw-cli to the latest allowed release

case "$SUBCMD" in
  setup)
    NEED_INSTALLER=1
    NEED_ENV_FILE=1
    NEED_DOCKER=1
    NEED_CLI_UPGRADE=1
    ;;
  start)
    NEED_ENV_FILE=1
    NEED_DOCKER=1
    NEED_CLI_UPGRADE=1
    ;;
  stop)
    # Stopping is a local teardown — no need to re-resolve the CLI from PyPI.
    NEED_ENV_FILE=1
    NEED_DOCKER=1
    ;;
  status|login|logout)
    NEED_CLI_UPGRADE=1
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
  if [[ "$OS_NAME" != "Linux" ]]; then
    die "Automatic package installation is only supported on Linux. Install manually: $*"
  fi
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

install_docker_desktop_via_homebrew() {
  have_cmd brew || die "Docker is not installed. Install Docker Desktop for Mac from https://docs.docker.com/desktop/setup/install/mac-install/ and rerun ./setup.sh."
  log_warn "Installing Docker Desktop via Homebrew"
  brew install --cask docker
  if have_cmd open; then
    log_warn "Starting Docker Desktop"
    open -a Docker || true
  fi
}

ensure_docker_installed() {
  if have_cmd docker; then
    return 0
  fi
  if (( NEED_INSTALLER )); then
    if [[ "$OS_NAME" == "Darwin" ]]; then
      confirm_install "Docker Desktop"
      install_docker_desktop_via_homebrew
      have_cmd docker || die "Docker Desktop installation did not put 'docker' on PATH. Restart your terminal or install Docker Desktop manually."
    else
      confirm_install "Docker Engine"
      install_docker_via_official_script
      have_cmd docker || die "Docker installation failed."
    fi
  else
    if [[ "$OS_NAME" == "Darwin" ]]; then
      die "Docker is not installed. Install Docker Desktop for Mac, then run ./setup.sh again."
    fi
    die "Docker is not installed. Run ./setup.sh first to install it."
  fi
}

ensure_docker_compose_installed() {
  if docker compose version &>/dev/null; then
    return 0
  fi
  if (( NEED_INSTALLER )); then
    if [[ "$OS_NAME" == "Darwin" ]]; then
      confirm_install "Docker Desktop (includes Docker Compose v2)"
      if ! have_cmd docker; then
        install_docker_desktop_via_homebrew
      fi
      docker compose version &>/dev/null || die "Docker Compose v2 was not found. Upgrade/reinstall Docker Desktop for Mac, then rerun ./setup.sh."
    else
      confirm_install "Docker Compose v2 plugin"
      case "$DISTRO_ID" in
        arch|manjaro|endeavouros) pkg_install docker-compose ;;
        *)                         pkg_install docker-compose-plugin ;;
      esac
      docker compose version &>/dev/null || die "Docker Compose v2 installation failed."
    fi
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
  if (( NEED_INSTALLER )) && [[ "$OS_NAME" == "Darwin" ]] && have_cmd open; then
    log_warn "Starting Docker Desktop"
    open -a Docker || true
    for _ in {1..90}; do
      if docker info &>/dev/null; then
        return 0
      fi
      sleep 2
    done
  fi
  if [[ "$OS_NAME" == "Darwin" ]]; then
    die "Docker daemon is not reachable. Start Docker Desktop and wait until it finishes starting."
  fi
  docker info &>/dev/null || die "Docker daemon is not reachable. Try: sudo systemctl start docker"
}

ensure_uv_installed() {
  if have_cmd uv; then
    return 0
  fi
  if (( NEED_INSTALLER )); then
    confirm_install "uv (Python package manager)"
    if ! have_cmd curl; then
      if [[ "$OS_NAME" == "Darwin" ]]; then
        have_cmd brew || die "curl is required to install uv. Install curl or Homebrew, then rerun ./setup.sh."
        brew install curl
      else
        pkg_install curl ca-certificates
      fi
    fi
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
  case "$OS_NAME" in
    Linux|Darwin) ;;
    *) die "This installer supports Linux and macOS only (detected '$OS_NAME'). Install docker, docker compose v2, and uv manually." ;;
  esac

  log_step "Checking hardware"
  case "$OS_NAME" in
    Linux)
      MEMINFO_PATH="${SETUP_MEMINFO_PATH:-/proc/meminfo}"
      if [[ -r "$MEMINFO_PATH" ]]; then
        MEM_KB="$(awk '/^MemTotal:/ {print $2; exit}' "$MEMINFO_PATH" || echo 0)"
        MEM_MB=$(( MEM_KB / 1024 ))
        if (( MEM_MB <= MIN_RAM_MB )); then
          die "Not enough RAM: detected ${MEM_MB} MB, SellerClaw requires more than ${MIN_RAM_MB} MB."
        fi
        log_ok "RAM: ${MEM_MB} MB (> ${MIN_RAM_MB} MB required)"
      else
        log_warn "Cannot read ${MEMINFO_PATH}; skipping RAM check."
      fi
      ;;
    Darwin)
      if have_cmd sysctl; then
        MEM_BYTES="$(sysctl -n hw.memsize 2>/dev/null || echo 0)"
        if [[ "$MEM_BYTES" =~ ^[0-9]+$ ]] && (( MEM_BYTES > 0 )); then
          MEM_MB=$(( MEM_BYTES / 1024 / 1024 ))
          if (( MEM_MB <= MIN_RAM_MB )); then
            die "Not enough RAM: detected ${MEM_MB} MB, SellerClaw requires more than ${MIN_RAM_MB} MB."
          fi
          log_ok "RAM: ${MEM_MB} MB (> ${MIN_RAM_MB} MB required)"
        else
          log_warn "Cannot read macOS memory size via sysctl; skipping RAM check."
        fi
      else
        log_warn "sysctl not found; skipping RAM check."
      fi
      ;;
  esac

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
# Resolve agent version from git tags (single source of truth, no hardcode).
# Forwarded to docker-compose as the SELLERCLAW_AGENT_VERSION build-arg and
# baked into the runtime image as ENV. Non-fatal: if git or tags are missing,
# the Dockerfile default ("0.0.0+unknown") will be used instead.
# ---------------------------------------------------------------------------

if [[ -z "${SELLERCLAW_AGENT_VERSION:-}" ]] && have_cmd git && [[ -d "$SCRIPT_DIR/.git" ]]; then
  agent_version="$(git -C "$SCRIPT_DIR" describe --tags --dirty --always --match 'v*' 2>/dev/null || true)"
  agent_version="${agent_version#v}"
  if [[ -n "$agent_version" ]]; then
    export SELLERCLAW_AGENT_VERSION="$agent_version"
  fi
fi

# When installed from a ZIP tarball (no .git folder) the build backend cannot
# read a version from VCS. pyproject.toml falls back to "0.0.0+unknown", but
# we warn the user so they know self-update and `./setup.sh start` after a
# `git pull` won't work — they'll need a proper `git clone` to upgrade.
if [[ ! -d "$SCRIPT_DIR/.git" ]] && (( NEED_INSTALLER || NEED_CLI_UPGRADE )); then
  log_warn "No .git directory detected — looks like a ZIP install."
  log_warn "  The agent will run with version '0.0.0+unknown' and you won't be"
  log_warn "  able to upgrade via 'git pull'. For updates, clone the repository:"
  log_warn "    git clone https://github.com/sellerai-com/sellerclaw-agent.git"
fi

# Paths containing non-ASCII characters or spaces frequently break Python
# packaging tools (uv build cache, hatchling, setuptools). Warn early so the
# user can move the project to a clean path before the first build attempt.
if [[ "$SCRIPT_DIR" =~ [^[:ascii:]] ]] || [[ "$SCRIPT_DIR" == *" "* ]]; then
  log_warn "Project path contains spaces or non-ASCII characters:"
  log_warn "    $SCRIPT_DIR"
  log_warn "  This can break Docker/uv/hatchling. If the install fails, move the"
  log_warn "  project to a path like '\$HOME/sellerclaw-agent' and retry."
fi

# ---------------------------------------------------------------------------
# Keep sellerclaw-cli current.
#
# `uv run` only syncs the venv to uv.lock; a newer release on PyPI does NOT make
# the lock stale, so uv would otherwise keep the pinned version forever. We ask
# uv to re-resolve sellerclaw-cli to the latest version allowed by pyproject
# before launching. Best-effort: set SELLERCLAW_NO_CLI_UPGRADE=1 to pin the
# locked version (reproducible/offline installs), and non-fatal when offline so
# cached commands keep working.
# ---------------------------------------------------------------------------

if (( NEED_CLI_UPGRADE )) && [[ -z "${SELLERCLAW_NO_CLI_UPGRADE:-}" ]]; then
  log_step "Updating SellerClaw CLI"
  if uv lock --upgrade-package sellerclaw-cli --quiet 2>/dev/null; then
    log_ok "sellerclaw-cli resolved to the latest allowed release"
  else
    log_warn "Could not refresh sellerclaw-cli (offline?); using the locked version."
  fi
fi

# ---------------------------------------------------------------------------
# Hand off to the CLI.
# ---------------------------------------------------------------------------

exec uv run --quiet sellerclaw-agent "${CLI_ARGS[@]:-setup}"
