#!/bin/sh
# SellerClaw Agent — one-command installer.
#
#   curl -fsSL https://get.sellerclaw.ai/agent.sh | sh
#
# Pulls the prebuilt runtime image from GHCR, starts it as a container, and signs the
# agent in to your SellerClaw account. No git checkout, no local image build, no Python
# on the host — the CLI, OpenClaw, the browser and sellerclaw-cli all live in the image.
#
# Re-running the same command upgrades an existing install: the image is pulled again and
# the container recreated. The data volume (account token, local secrets) is kept, so you
# are not asked to sign in twice. OpenClaw's own state is restored from the cloud backup
# on the next start, exactly like a managed agent.
#
# Usage:
#   sh install.sh [--version X.Y.Z | --beta] [--env production|staging|local]
#                 [--yes] [--no-login] [--dry-run] [--uninstall] [--help]
#
# Contributors working on the agent itself want the repo checkout and ./setup.sh instead
# (it builds the image from source) — see docs/cli.md.
#
# POSIX sh on purpose: `curl … | sh` is not bash. Two consequences shape this file —
# stdin belongs to the pipe (so every question is asked on /dev/tty), and the whole
# script is wrapped in main() called on the last line, so a truncated download cannot
# execute a half-read script.

set -eu

IMAGE_REPO="${SELLERCLAW_IMAGE_REPO:-ghcr.io/sellerai-com/sellerclaw-agent}"
INSTALL_URL="${SELLERCLAW_INSTALL_URL:-https://get.sellerclaw.ai/agent.sh}"
CONTAINER_NAME="${SELLERCLAW_CONTAINER_NAME:-sellerclaw-agent}"
DATA_VOLUME="${SELLERCLAW_DATA_VOLUME:-sellerclaw-agent-data}"
HOME_DIR="${SELLERCLAW_HOME:-${HOME:-/root}/.sellerclaw-agent}"

# Control plane and the agent's browser view. Both are published on loopback only: the
# agent needs no inbound traffic from anywhere (it polls the cloud outbound), and these
# ports would otherwise be reachable from the local network.
CONTROL_PORT=8001
VNC_PORT=6080

MIN_RAM_MB=2048
HEALTH_TIMEOUT_S=240

IMAGE_TAG="latest"
PROFILE="production"
ASSUME_YES=0
DRY_RUN=0
DO_LOGIN=1
PURGE=0
ACTION="install"
# Flags worth replaying on `sellerclaw-agent update`.
SAVED_FLAGS=""

# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

if [ -t 1 ]; then
    C_STEP='\033[1;36m'
    C_OK='\033[0;32m'
    C_WARN='\033[0;33m'
    C_ERR='\033[0;31m'
    C_OFF='\033[0m'
else
    C_STEP='' C_OK='' C_WARN='' C_ERR='' C_OFF=''
fi

log_step() { printf "\n${C_STEP}==>${C_OFF} %s\n" "$*"; }
log_ok()   { printf "    ${C_OK}✓${C_OFF} %s\n" "$*"; }
log_warn() { printf "    ${C_WARN}!${C_OFF} %s\n" "$*"; }
log_err()  { printf "    ${C_ERR}✗${C_OFF} %s\n" "$*" >&2; }
die()      { log_err "$*"; exit 1; }

have_cmd() { command -v "$1" >/dev/null 2>&1; }

usage() {
    cat <<'EOF'
SellerClaw Agent installer

Usage:
  curl -fsSL https://get.sellerclaw.ai/agent.sh | sh
  sh install.sh [options]

Options:
  --version X.Y.Z   Install a specific agent version (default: the latest release).
  --beta            Install the current pre-release build instead of the stable one.
  --env <profile>   SellerClaw cloud to connect to: production (default), staging, local.
  --yes             Answer yes to every question (needed when there is no terminal).
  --no-login        Start the agent but skip the sign-in step.
  --dry-run         Print what would be done without touching the system.
  --uninstall       Remove the agent container and the sellerclaw-agent command.
  --purge           With --uninstall: also delete the stored data (sign-in is then lost).
  --help            Show this message.

After the install, manage the agent with:
  sellerclaw-agent status | logs | stop | start | update | uninstall
EOF
}

has_terminal() {
    # `-r /dev/tty` is not enough: the device node exists inside containers and CI runners
    # while *opening* it fails ("no such device or address") when the process has no
    # controlling terminal. Only an actual open answers the question — and it has to happen
    # in a subshell, because a redirection error on a special built-in (`:`) terminates the
    # shell outright under `set -e`, which would end the install with no message at all.
    ( : >/dev/tty ) 2>/dev/null
}

# `curl | sh` hands the script its own bytes on stdin, so questions must go to the
# terminal directly. Without a terminal (CI, provisioning scripts) --yes is required
# rather than silently assuming consent for a privileged install.
confirm() {
    if [ "$ASSUME_YES" -eq 1 ]; then
        return 0
    fi
    if ! has_terminal; then
        die "$1 — no terminal to ask on. Re-run with --yes to accept, or do this step yourself."
    fi
    printf "    %s [Y/n] " "$1" >/dev/tty
    # A bare Enter means the default (yes); end-of-input means nobody answered, which is a no.
    read -r reply </dev/tty || return 1
    case "$reply" in
        Y|y|yes|YES|"") return 0 ;;
        *) return 1 ;;
    esac
}

# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------

parse_args() {
    while [ $# -gt 0 ]; do
        case "$1" in
            --version)
                [ $# -ge 2 ] || die "--version requires a value, e.g. --version 0.56.0"
                IMAGE_TAG="${2#v}"
                SAVED_FLAGS="$SAVED_FLAGS --version $IMAGE_TAG"
                shift 2
                ;;
            --version=*)
                IMAGE_TAG="${1#--version=}"
                IMAGE_TAG="${IMAGE_TAG#v}"
                SAVED_FLAGS="$SAVED_FLAGS --version $IMAGE_TAG"
                shift
                ;;
            --beta)
                IMAGE_TAG="beta"
                SAVED_FLAGS="$SAVED_FLAGS --beta"
                shift
                ;;
            --env)
                [ $# -ge 2 ] || die "--env requires a value (production, staging, local)"
                PROFILE="$2"
                SAVED_FLAGS="$SAVED_FLAGS --env $PROFILE"
                shift 2
                ;;
            --env=*)
                PROFILE="${1#--env=}"
                SAVED_FLAGS="$SAVED_FLAGS --env $PROFILE"
                shift
                ;;
            -y|--yes)      ASSUME_YES=1; shift ;;
            --no-login)    DO_LOGIN=0; shift ;;
            --dry-run)     DRY_RUN=1; shift ;;
            --uninstall)   ACTION="uninstall"; shift ;;
            --purge)       PURGE=1; shift ;;
            -h|--help)     usage; exit 0 ;;
            *)             die "Unknown option: $1 (see --help)" ;;
        esac
    done

    case "$PROFILE" in
        production)
            API_URL="https://api.sellerclaw.ai"
            WEB_URL="https://app.sellerclaw.ai"
            ;;
        staging)
            API_URL="https://sellerclaw-staging.fly.dev"
            WEB_URL="https://staging-app.sellerclaw.ai"
            ;;
        local)
            API_URL="http://host.docker.internal:8000"
            WEB_URL="http://localhost:5173"
            ;;
        *)
            die "Invalid --env '$PROFILE' (expected: production, staging, local)"
            ;;
    esac

    IMAGE="$IMAGE_REPO:$IMAGE_TAG"
}

# ---------------------------------------------------------------------------
# Docker command runner (the single place --dry-run intercepts)
# ---------------------------------------------------------------------------

run_docker() {
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '+ docker'
        for arg in "$@"; do
            printf ' %s' "$arg"
        done
        printf '\n'
        return 0
    fi
    docker "$@"
}

# ---------------------------------------------------------------------------
# Host checks and dependencies
# ---------------------------------------------------------------------------

sudo_cmd() {
    if [ "$(id -u)" = "0" ]; then
        "$@"
    elif have_cmd sudo; then
        sudo "$@"
    else
        die "This step needs root and sudo is missing. Re-run as root: $*"
    fi
}

check_platform() {
    os_name="$(uname -s)"
    case "$os_name" in
        Linux|Darwin) ;;
        *) die "This installer supports Linux and macOS only (detected '$os_name')." ;;
    esac

    arch="$(uname -m)"
    case "$arch" in
        x86_64|amd64|arm64|aarch64) log_ok "platform: $os_name $arch" ;;
        *) die "Unsupported CPU architecture '$arch'. SellerClaw Agent images are built for x86_64 and arm64." ;;
    esac
}

check_memory() {
    os_name="$(uname -s)"
    mem_mb=0
    if [ "$os_name" = "Linux" ]; then
        meminfo="${SETUP_MEMINFO_PATH:-/proc/meminfo}"
        if [ -r "$meminfo" ]; then
            mem_kb="$(awk '/^MemTotal:/ {print $2; exit}' "$meminfo" 2>/dev/null || echo 0)"
            case "$mem_kb" in
                ''|*[!0-9]*) mem_kb=0 ;;
            esac
            mem_mb=$((mem_kb / 1024))
        fi
    elif have_cmd sysctl; then
        mem_bytes="$(sysctl -n hw.memsize 2>/dev/null || echo 0)"
        case "$mem_bytes" in
            ''|*[!0-9]*) mem_bytes=0 ;;
        esac
        mem_mb=$((mem_bytes / 1024 / 1024))
    fi

    if [ "$mem_mb" -le 0 ]; then
        log_warn "Could not read the amount of RAM; skipping the check."
        return 0
    fi
    if [ "$mem_mb" -le "$MIN_RAM_MB" ]; then
        die "Not enough RAM: ${mem_mb} MB detected, SellerClaw Agent needs more than ${MIN_RAM_MB} MB."
    fi
    log_ok "RAM: ${mem_mb} MB"
}

install_docker() {
    os_name="$(uname -s)"
    if [ "$os_name" = "Darwin" ]; then
        have_cmd brew || die "Docker is not installed. Install Docker Desktop from https://docs.docker.com/desktop/setup/install/mac-install/ and run this command again."
        confirm "Install Docker Desktop with Homebrew?" || die "Docker is required."
        brew install --cask docker
        have_cmd open && open -a Docker || true
        return 0
    fi
    confirm "Docker is missing. Install it now (uses the official get.docker.com script)?" \
        || die "Docker is required. Install it and run this command again."
    have_cmd curl || die "curl is required to install Docker. Install curl and try again."
    tmp_script="$(mktemp)"
    curl -fsSL https://get.docker.com -o "$tmp_script"
    sudo_cmd sh "$tmp_script"
    rm -f "$tmp_script"
    have_cmd docker || die "Docker installation did not put 'docker' on PATH."
}

ensure_docker_running() {
    if docker info >/dev/null 2>&1; then
        log_ok "docker: $(docker --version 2>/dev/null || echo present)"
        return 0
    fi
    os_name="$(uname -s)"
    if [ "$os_name" = "Darwin" ]; then
        if have_cmd open; then
            log_warn "Starting Docker Desktop…"
            open -a Docker || true
            waited=0
            while [ "$waited" -lt 120 ]; do
                if docker info >/dev/null 2>&1; then
                    log_ok "docker daemon is running"
                    return 0
                fi
                sleep 3
                waited=$((waited + 3))
            done
        fi
        die "Docker daemon is not reachable. Start Docker Desktop, wait until it finishes starting, then run this command again."
    fi
    if have_cmd systemctl; then
        log_warn "Starting the docker service…"
        sudo_cmd systemctl enable --now docker || true
    fi
    docker info >/dev/null 2>&1 || die "Docker daemon is not reachable. Try: sudo systemctl start docker"
    log_ok "docker daemon is running"
}

check_docker() {
    if [ "$DRY_RUN" -eq 1 ]; then
        return 0
    fi
    if ! have_cmd docker; then
        install_docker
    fi
    ensure_docker_running

    # Rootless setups and "user not in the docker group" both surface here rather than
    # halfway through the install with a raw permission error.
    if ! docker ps >/dev/null 2>&1; then
        die "Cannot talk to Docker as this user. Add yourself to the docker group (sudo usermod -aG docker \$USER, then log out and back in) or re-run with sudo."
    fi
}

# ---------------------------------------------------------------------------
# Install steps
# ---------------------------------------------------------------------------

pull_image() {
    log_step "Downloading SellerClaw Agent ($IMAGE)"
    if run_docker pull "$IMAGE"; then
        log_ok "image ready"
        return 0
    fi
    arch="$(uname -m)"
    case "$arch" in
        arm64|aarch64)
            die "Could not download the image for this machine ($arch). If the error mentions a missing manifest, this version has no arm64 build yet — install a newer version, or report it at https://github.com/sellerai-com/sellerclaw-agent/issues."
            ;;
        *)
            die "Could not download the image. Check your internet connection and try again."
            ;;
    esac
}

container_exists() {
    docker ps -a --format '{{.Names}}' 2>/dev/null | grep -Fxq "$CONTAINER_NAME"
}

container_running() {
    [ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER_NAME" 2>/dev/null || echo false)" = "true" ]
}

start_container() {
    if [ "$DRY_RUN" -eq 0 ] && container_exists; then
        log_step "Replacing the previous container (your data volume is kept)"
        docker rm -f "$CONTAINER_NAME" >/dev/null
    fi

    log_step "Starting SellerClaw Agent"
    # Only /data is persisted: it holds the account token and local secrets, so an upgrade
    # does not ask you to sign in again. OpenClaw's own state directory is deliberately NOT
    # a volume — it ships inside the image (plugins, extensions) and would be shadowed by a
    # stale copy after an upgrade; sessions and memory come back from the cloud backup.
    set -- run -d \
        --name "$CONTAINER_NAME" \
        --restart unless-stopped \
        -p "127.0.0.1:$CONTROL_PORT:8001" \
        -p "127.0.0.1:$VNC_PORT:6080" \
        -v "$DATA_VOLUME:/data" \
        --add-host "host.docker.internal:host-gateway" \
        -e "SELLERCLAW_API_URL=$API_URL" \
        -e "SELLERCLAW_WEB_URL=$WEB_URL" \
        -e "SELLERCLAW_DATA_DIR=/data" \
        "$IMAGE"
    if [ "$DRY_RUN" -eq 1 ]; then
        run_docker "$@"
    else
        run_docker "$@" >/dev/null \
            || die "Could not start the container. See: docker logs $CONTAINER_NAME"
    fi
    log_ok "container '$CONTAINER_NAME' started"
}

wait_until_ready() {
    if [ "$DRY_RUN" -eq 1 ]; then
        return 0
    fi
    log_step "Waiting for the agent to come up"
    waited=0
    while [ "$waited" -lt "$HEALTH_TIMEOUT_S" ]; do
        if docker exec "$CONTAINER_NAME" curl -fsS "http://127.0.0.1:8001/health" >/dev/null 2>&1; then
            log_ok "agent is up"
            return 0
        fi
        if ! container_running; then
            log_err "The container stopped right after starting. Last lines of its log:"
            docker logs --tail 30 "$CONTAINER_NAME" 2>&1 | sed 's/^/      /' || true
            die "SellerClaw Agent could not start."
        fi
        sleep 3
        waited=$((waited + 3))
    done
    log_err "The agent did not report ready within $((HEALTH_TIMEOUT_S / 60)) minutes. Last lines of its log:"
    docker logs --tail 30 "$CONTAINER_NAME" 2>&1 | sed 's/^/      /' || true
    die "Giving up. Check the log above, then run: sellerclaw-agent logs"
}

# ---------------------------------------------------------------------------
# The `sellerclaw-agent` command
# ---------------------------------------------------------------------------

wrapper_dir() {
    if [ -n "${SELLERCLAW_BIN_DIR:-}" ]; then
        printf '%s' "$SELLERCLAW_BIN_DIR"
        return 0
    fi
    printf '%s' "${HOME:-/root}/.local/bin"
}

install_wrapper() {
    bin_dir="$(wrapper_dir)"
    wrapper="$bin_dir/sellerclaw-agent"
    log_step "Installing the 'sellerclaw-agent' command"
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '+ write %s\n' "$wrapper"
        return 0
    fi
    mkdir -p "$bin_dir" "$HOME_DIR"

    # Keep a copy of this installer next to the data dir: `update` and `uninstall` reuse it
    # when the machine is offline or the download URL is unreachable.
    if [ -f "$0" ] && [ -r "$0" ]; then
        cp "$0" "$HOME_DIR/install.sh" 2>/dev/null || true
    elif have_cmd curl; then
        curl -fsSL "$INSTALL_URL" -o "$HOME_DIR/install.sh" 2>/dev/null || true
    fi
    if [ -f "$HOME_DIR/install.sh" ]; then
        chmod +x "$HOME_DIR/install.sh"
    fi

    tmp_wrapper="$(mktemp)"
    {
        printf '#!/bin/sh\n'
        printf '# SellerClaw Agent control command. Generated by the installer — rewritten on every update.\n'
        printf 'set -eu\n'
        printf 'CONTAINER_NAME=%s\n' "$CONTAINER_NAME"
        printf 'INSTALL_URL=%s\n' "$INSTALL_URL"
        printf 'LOCAL_INSTALLER=%s/install.sh\n' "$HOME_DIR"
        printf 'INSTALL_FLAGS="%s"\n' "$SAVED_FLAGS"
        printf 'VNC_URL=http://127.0.0.1:%s\n' "$VNC_PORT"
        cat <<'WRAPPER'

agent_cli() {
    # The CLI lives inside the image (/app), so nothing Python-related is needed on the host.
    tty_flag=""
    [ -t 1 ] && tty_flag="-t"
    # shellcheck disable=SC2086
    exec docker exec -i $tty_flag -w /app "$CONTAINER_NAME" python -m sellerclaw_agent "$@"
}

reinstall() {
    if command -v curl >/dev/null 2>&1 && curl -fsSL "$INSTALL_URL" -o "$LOCAL_INSTALLER.new" 2>/dev/null; then
        mv "$LOCAL_INSTALLER.new" "$LOCAL_INSTALLER"
        chmod +x "$LOCAL_INSTALLER"
    fi
    if [ ! -f "$LOCAL_INSTALLER" ]; then
        printf 'Installer not found. Re-run: curl -fsSL %s | sh\n' "$INSTALL_URL" >&2
        exit 1
    fi
    # shellcheck disable=SC2086
    exec sh "$LOCAL_INSTALLER" $INSTALL_FLAGS "$@"
}

case "${1:-help}" in
    start)     exec docker start "$CONTAINER_NAME" ;;
    stop)      exec docker stop "$CONTAINER_NAME" ;;
    restart)   exec docker restart "$CONTAINER_NAME" ;;
    logs)      shift; exec docker logs -f --tail "${1:-100}" "$CONTAINER_NAME" ;;
    status)    agent_cli status ;;
    login)     agent_cli login --browser ;;
    logout)    agent_cli logout ;;
    browser)   printf 'Open %s in your browser to watch the agent work.\n' "$VNC_URL" ;;
    version)   exec docker exec "$CONTAINER_NAME" printenv SELLERCLAW_AGENT_VERSION ;;
    update)    shift; reinstall --yes "$@" ;;
    uninstall) shift; reinstall --uninstall "$@" ;;
    help|--help|-h)
        cat <<'USAGE'
sellerclaw-agent — control the local SellerClaw agent

  status      Show whether the agent is connected to your account
  login       Sign in to your SellerClaw account
  logout      Disconnect from your account
  start       Start the agent container
  stop        Stop the agent container
  restart     Restart the agent container
  logs [N]    Follow the container log (last N lines, default 100)
  browser     Print the address where you can watch the agent's browser
  version     Show the installed agent version
  update      Download the latest version and restart
  uninstall   Remove the agent from this machine
USAGE
        ;;
    *)
        printf 'Unknown command: %s (try: sellerclaw-agent help)\n' "$1" >&2
        exit 2
        ;;
esac
WRAPPER
    } >"$tmp_wrapper"
    chmod +x "$tmp_wrapper"
    mv "$tmp_wrapper" "$wrapper"
    log_ok "command installed: $wrapper"

    case ":${PATH}:" in
        *":$bin_dir:"*) ;;
        *) log_warn "$bin_dir is not in your PATH — add it, or call the command by full path: $wrapper" ;;
    esac
}

# ---------------------------------------------------------------------------
# Sign-in
# ---------------------------------------------------------------------------

supports_container_cli() {
    # Images older than the one-command install ship the package without a module entry point,
    # so `python -m sellerclaw_agent` fails there. Ask before signing in, so `--version <old>`
    # produces one clear sentence instead of a Python traceback.
    docker exec -w /app "$CONTAINER_NAME" python -c "import sellerclaw_agent.__main__" \
        >/dev/null 2>&1
}

already_connected() {
    docker exec -w /app "$CONTAINER_NAME" python -m sellerclaw_agent status 2>/dev/null \
        | grep -q "Connected"
}

sign_in() {
    if [ "$DRY_RUN" -eq 1 ] || [ "$DO_LOGIN" -eq 0 ]; then
        return 0
    fi
    if ! supports_container_cli; then
        log_warn "This agent version is too old to sign in from here (image $IMAGE)."
        log_warn "Install a newer one — drop --version to get the latest release — and run it again."
        return 0
    fi
    if already_connected; then
        log_step "Your account"
        log_ok "already signed in — keeping the existing connection"
        return 0
    fi
    log_step "Connecting the agent to your SellerClaw account"
    tty_flag=""
    [ -t 1 ] && tty_flag="-t"
    # shellcheck disable=SC2086
    if ! docker exec -i $tty_flag -w /app "$CONTAINER_NAME" python -m sellerclaw_agent login --browser; then
        log_warn "Sign-in did not finish. The agent is installed and running — finish it any time with: sellerclaw-agent login"
    fi
}

# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------

uninstall() {
    log_step "Removing SellerClaw Agent"
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '+ docker rm -f %s\n' "$CONTAINER_NAME"
        printf '+ rm %s/sellerclaw-agent\n' "$(wrapper_dir)"
        return 0
    fi
    if have_cmd docker && container_exists; then
        docker rm -f "$CONTAINER_NAME" >/dev/null
        log_ok "container removed"
    else
        log_ok "no container to remove"
    fi

    # Stored data (the account token) is only deleted when asked for explicitly: --purge, or
    # a "yes" typed at the prompt. A scripted run (--yes, no terminal) keeps it — losing the
    # sign-in must never be a side effect of automating an uninstall.
    if have_cmd docker && docker volume inspect "$DATA_VOLUME" >/dev/null 2>&1; then
        delete_data=0
        if [ "$PURGE" -eq 1 ]; then
            delete_data=1
        elif [ "$ASSUME_YES" -eq 0 ] && has_terminal; then
            if confirm "Also delete stored data (you would have to sign in again)?"; then
                delete_data=1
            fi
        fi
        if [ "$delete_data" -eq 1 ]; then
            docker volume rm "$DATA_VOLUME" >/dev/null
            log_ok "data volume removed"
        else
            log_ok "data kept — delete it later with: docker volume rm $DATA_VOLUME"
        fi
    fi

    wrapper="$(wrapper_dir)/sellerclaw-agent"
    if [ -f "$wrapper" ]; then
        rm -f "$wrapper"
        log_ok "command removed"
    fi
    if [ -d "$HOME_DIR" ]; then
        rm -rf "$HOME_DIR"
    fi
    printf "\n${C_OK}SellerClaw Agent has been removed.${C_OFF}\n"
    printf "Your account and your data in the SellerClaw cloud are untouched — you can switch back to the hosted agent in the web panel.\n\n"
}

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

final_message() {
    printf "\n${C_OK}SellerClaw Agent is installed and running.${C_OFF}\n\n"
    printf "  Web panel:      %s\n" "$WEB_URL"
    printf "  Agent browser:  http://127.0.0.1:%s\n" "$VNC_PORT"
    printf "  Manage it with: sellerclaw-agent status | logs | stop | update\n\n"
    printf "In the web panel open Settings → Hosting and switch to self-hosted, so your tasks run on this machine.\n\n"
}

main() {
    parse_args "$@"

    if [ "$ACTION" = "uninstall" ]; then
        uninstall
        return 0
    fi

    printf "\n${C_STEP}SellerClaw Agent${C_OFF} — installing %s\n" "$IMAGE"

    log_step "Checking this machine"
    check_platform
    check_memory
    check_docker

    pull_image
    start_container
    wait_until_ready
    install_wrapper
    sign_in
    final_message
}

main "$@"
