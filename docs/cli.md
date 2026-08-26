# `sellerclaw-agent` CLI

Command-line tool for the local edge agent: it brings up the runtime and connects the agent to the SellerClaw cloud. **All auth traffic goes to the local Agent Server only** (not directly to the cloud) — your credentials never leave your machine without passing through the agent process first.

This page covers both installation paths, environments, every command, and common failure modes.

There are two ways to run the agent, and they differ in exactly one thing — where the image comes from:

| | Install (users) | From source (contributors) |
|---|---|---|
| Command | `curl -fsSL https://get.sellerclaw.ai/agent.sh \| sh` | `./setup.sh` in a checkout |
| Image | downloaded from GHCR | built locally from `runtime/Dockerfile` |
| Needs on the host | Docker | Docker + Compose v2, Python 3.12+, uv, the repo |
| Runs the container with | `docker run` | `docker compose` |
| Day-to-day commands | `sellerclaw-agent …` | `./setup.sh …` |

Both end up running the same image, talk to the same control plane on `127.0.0.1:8001`, and use the same sign-in flow.

## Install (one command)

```bash
curl -fsSL https://get.sellerclaw.ai/agent.sh | sh
```

The script checks the machine (Linux or macOS, x86_64 or arm64, more than 2 GB of RAM), installs Docker if it is missing, downloads `ghcr.io/sellerai-com/sellerclaw-agent:latest`, starts the container, writes the `sellerclaw-agent` command, and runs the browser sign-in.

To read it before running it: `curl -fsSL https://get.sellerclaw.ai/agent.sh -o agent.sh`, read, then `sh agent.sh`. The same file lives in this repository as [`install.sh`](../install.sh).

Options:

| Option | Effect |
|---|---|
| `--version X.Y.Z` | Install a specific version instead of the latest release. |
| `--beta` | Install the current pre-release build (the `:beta` tag). |
| `--env staging` \| `local` | Point the agent at a non-production SellerClaw cloud. |
| `--yes` | Answer yes to every question — required when there is no terminal. |
| `--no-login` | Start the agent but skip sign-in (do it later with `sellerclaw-agent login`). |
| `--dry-run` | Print the exact docker commands without running anything. |
| `--uninstall` | Remove the container and the `sellerclaw-agent` command. |
| `--purge` | With `--uninstall`: also delete the data volume (sign-in is lost). |

What it creates:

- container `sellerclaw-agent`, restart policy `unless-stopped`, ports published on loopback only (`127.0.0.1:8001` control plane, `127.0.0.1:6080` the agent's browser view);
- volume `sellerclaw-agent-data` mounted at `/data` — the account token and local secrets, which is why an upgrade never asks you to sign in again;
- `~/.local/bin/sellerclaw-agent` — a thin wrapper around `docker`, plus a copy of the installer under `~/.sellerclaw-agent/`.

OpenClaw's own state directory is deliberately *not* a volume: it ships inside the image, and a stale copy would shadow the plugins and extensions of a newer version. Chats live in the SellerClaw database and durable facts in long-term memory, so nothing there needs to survive locally; browser sign-ins are the one exception and come back from the cloud backup on the next start, exactly like a managed agent.

### Day-to-day commands

```bash
sellerclaw-agent status      # connected to your account?
sellerclaw-agent login       # sign in (prints a code and a link)
sellerclaw-agent logout      # disconnect this agent from the account
sellerclaw-agent logs 200    # follow the container log
sellerclaw-agent stop|start|restart
sellerclaw-agent browser     # where to watch the agent's browser
sellerclaw-agent version     # installed agent version
sellerclaw-agent update      # pull the newest image and recreate the container
sellerclaw-agent uninstall   # remove it (add --purge to delete stored data too)
```

`status`, `login` and `logout` run the very CLI documented below — inside the container, through `docker exec`, so the host needs no Python.

## Run from source (contributors)

Requirements:

- **Docker** and **Docker Compose v2** (`docker compose version` must succeed). On macOS, install/start Docker Desktop.
- **Python 3.12+** (only for running the CLI itself; the agent services run inside Docker).
- **Combined runtime image** — `docker compose` builds a single image (OpenClaw browser stack + SellerClaw agent) from `runtime/Dockerfile` target `staging`.

The image is built automatically on first run. To pre-build or publish your own copy, see [Building the runtime image](#building-the-runtime-image) below.

Optional: set `OPENCLAW_RUNTIME_IMAGE` to a tag for display in `GET /openclaw/status`.

One command from the `sellerclaw-agent/` directory:

```bash
cd sellerclaw-agent
./setup.sh
```

This script checks for Docker, installs Python dependencies with `uv`, brings up the Docker stack, and starts the interactive sign-in — all in a single step. On macOS, it can install Docker Desktop through Homebrew when Homebrew is available.

Alternatively, if you already have [uv](https://docs.astral.sh/uv/) installed:

```bash
cd sellerclaw-agent
uv run sellerclaw-agent setup
```

Or via Make:

```bash
cd sellerclaw-agent
make setup
```

## Environments

The agent supports multiple environment profiles. Each profile is a `.env.<name>` file in the repo root (committed, non-secret) that controls which SellerClaw cloud the agent connects to. **Secrets** — especially `SELLERCLAW_LOCAL_API_KEY` and `AGENT_API_KEY` — belong in `secrets.env` at the repo root (gitignored). Copy `secrets.env.example` to `secrets.env` and edit there.

`docker compose` and the CLI pass `--env-file` for the profile and, when the file exists, `--env-file secrets.env`. If `secrets.env` is missing, only the profile file is used (the local API key and OpenClaw gateway/hooks tokens are then auto-generated under `data/secrets.json`, with a one-time migration from legacy `data/local_api_key` when present, unless you override them via env vars).

| File | Role |
|------|------|
| `.env.local` | Local development — cloud URLs (`http://host.docker.internal:8000`, …) |
| `.env.staging` | Staging cloud |
| `.env.production` | Production cloud |
| `secrets.env` | Local secrets (`SELLERCLAW_LOCAL_API_KEY`, `AGENT_API_KEY`, …) |

### Switching environments

Pass `--env <name>` to `setup.sh`:

```bash
./setup.sh --env staging
./setup.sh --env production
```

Or export `AGENT_ENV` before any command:

```bash
export AGENT_ENV=staging
./setup.sh
# or
uv run sellerclaw-agent status
```

When `AGENT_ENV` is not set, `.env.production` is used by default (see `setup.sh`).

### Creating a custom profile

Copy any existing file and adjust the values:

```bash
cp .env.staging .env.custom
# edit .env.custom
./setup.sh --env custom
```

### Secrets file

```bash
cp secrets.env.example secrets.env
# set SELLERCLAW_LOCAL_API_KEY and/or AGENT_API_KEY as needed
```

## Environment variables

Non-secret variables live in `.env.local` / `.env.staging` / `.env.production`. Sensitive values live in `secrets.env`. Key settings:

| Variable | Purpose | Typical source |
|----------|---------|----------------|
| `SELLERCLAW_API_URL` | Cloud API the agent server talks to | Profile `.env.*` |
| `SELLERCLAW_WEB_URL` | SellerClaw website that hosts the `/auth/device` verification page | Profile `.env.*` |
| `SELLERCLAW_LOCAL_API_KEY` | **Incoming** Bearer for control-plane routes (`/manifest`, `/auth/*`, `/bundle/archive`, `/openclaw/*`, `/commands/history`, …) on port `8001` | `secrets.env` or unset (stored in `SELLERCLAW_DATA_DIR/secrets.json`; legacy `local_api_key` file is migrated once) |
| `AGENT_API_KEY` | **Outgoing** Bearer for the SellerClaw cloud (`/agent/connection/*`, chat SSE, etc.) — same role as the token in `agent_token.json` | `secrets.env` or sign-in |
| `SELLERCLAW_DATA_DIR` | Where the agent stores `agent_token.json`, `secrets.json`, `edge_session.json`, manifest (and legacy `local_api_key` until migrated) | `/data` (inside the container) |
| `SELLERCLAW_EDGE_PING` | Enable the background ping loop (cloud mode) | `1` |
| `SELLERCLAW_AGENT_IMAGE` | Pin a specific runtime image tag instead of building locally | *(unset)* |

The agent server always listens on port `8001` inside the container. By default compose publishes it as **`127.0.0.1:8001`** on the host (loopback only); the CLI reaches it at `http://127.0.0.1:8001`.

See the [cloud connection protocol](./connection-protocol.md) for how the ping loop uses `SELLERCLAW_API_URL` and `SELLERCLAW_DATA_DIR`.

## Commands

| Command | Description |
|---------|-------------|
| `setup` | **Default** when no argument is given: `docker compose up -d --build`, wait for `GET /health`, interactive cloud sign-in. |
| `start` | Start the stack only: `docker compose up -d --build` in the agent directory. |
| `stop` | Stop the stack: `docker compose down`. |
| `status` | Show whether the agent is connected to the cloud (`GET /auth/status`). |
| `login` | Sign in to the cloud (server must be running): up to 15 s wait for the agent, then the same interactive flow as `setup`. `login --browser` skips the menu and signs in by link. |
| `logout` | Clear stored cloud credentials on the agent (`POST /auth/disconnect`). |
| `help` | Short help. Same idea: `-h`, `--help`, `help`. |

Unknown command: exit code `2`.

`setup`, `start` and `stop` drive `docker compose` and therefore only work in a checkout. The rest work anywhere the CLI can reach the control plane — including inside the container, which is how the one-command install runs them:

```bash
docker exec -w /app sellerclaw-agent python -m sellerclaw_agent status
```

## Signing in to the cloud (interactive)

For `setup` or `login` you can choose:

1. **Email and password** — sent to the local agent at `POST /auth/connect`; the agent talks to the cloud.
2. **Browser (device flow)** — the agent requests codes (`POST /auth/device/start`); the terminal shows the user code and verification link; the CLI polls `GET /auth/device/poll?device_code=...` until success or timeout. In the browser, sign in to SellerClaw and approve the device.

`login --browser` goes straight to the second option and never opens a browser locally — the path taken when the CLI runs inside the container, where there is no menu to answer and the only browser is the agent's own.

## Where the CLI looks for `docker-compose.yml`

Compose runs in the **parent directory of the installed `sellerclaw_agent` package**; `docker-compose.yml` is expected next to that directory.

- With an **editable** install from the repo (`pip install -e .` / `uv sync` from `sellerclaw-agent/`), that directory is the `sellerclaw-agent/` root and matches the repository.
- With a **wheel-only** install and no repo checkout, the path may resolve under `site-packages`, where there is **no** `docker-compose.yml`. For `setup` / `start` / `stop`, use a repo checkout with an editable install.

## Building the runtime image

The combined OpenClaw + agent image is built from [`runtime/Dockerfile`](../runtime/Dockerfile) with target `staging`. For local development `docker compose` builds it automatically.

To build the image yourself, run from the monorepo root:

```bash
docker build \
  -f sellerclaw-agent/runtime/Dockerfile \
  --target staging \
  -t sellerclaw-agent:latest .
```

To publish to a registry (for example GHCR), tag the result with your `ghcr.io/<owner>/<image>:<tag>` and use `docker push` after `docker login` to that registry.

### Using a local `sellerclaw-cli` build (contributors)

By default the edge `sellerclaw` command (and the host CLI) are installed from PyPI, so `./setup.sh` connects to production and uses released tools — no extra setup. If you are iterating on `sellerclaw-cli` itself, you can build the agent against a local source checkout instead of publishing to PyPI first:

```bash
cp dev.env.example dev.env
# edit dev.env: SELLERCLAW_CLI_LOCAL_PATH=/abs/path/to/sellerclaw-cli
make up-dev          # builds the edge image with sellerclaw-cli from your checkout
make install         # also links it (editable) into the host venv
```

How it works:

- `make` reads `dev.env` (gitignored, per-developer) and, when `SELLERCLAW_CLI_LOCAL_PATH` is set, builds a wheel into `runtime/.local-wheels/` and passes `SELLERCLAW_CLI_SOURCE=local` to the build. The image installs that wheel over the locked release.
- This is **independent of the environment profile**: combine it with `--env local|staging|production` (via `make up-dev`/`up-stage`/`up`) to choose the SellerClaw backend separately from the CLI version.
- `./setup.sh` never reads `dev.env` and never sets the build arg, so the user/production path and CI stay on PyPI. The wheel directory is committed empty (`.gitkeep`); only `*.whl` is ignored.
- Rebuild (`make up-dev`) to pick up edits in the edge image. For the host CLI, re-run `make dev-cli-local` after any `uv sync`/`uv run` (those revert the editable link), or invoke it as `uv run --no-sync sellerclaw …`. Use `make dev-cli-pypi` to revert the host venv to the locked release.

## Troubleshooting

### First-run failures

- **`Docker Compose v2 not found`** — install the Compose plugin and verify `docker compose version`. On Linux the plugin ships as the `docker-compose-plugin` package; on macOS it is bundled with Docker Desktop.
- **macOS: `Docker daemon is not reachable`** — open Docker Desktop and wait until it finishes starting, then rerun `./setup.sh`.
- **`permission denied while trying to connect to the Docker daemon`** — add your user to the `docker` group (`sudo usermod -aG docker $USER`) and start a new shell, or run `docker` with `sudo` temporarily.
- **First `setup` takes a very long time** — the runtime image is large (~1.5 GB). The initial `docker compose up --build` pulls the OpenClaw base image and installs Chromium, KasmVNC, supervisord, and Playwright. Subsequent runs are fast.

### After setup

- **Timeout waiting for the agent after setup** (`GET /health` during `setup`) — inspect logs with `docker compose logs` in `sellerclaw-agent/`. The most common causes are the server still booting (wait another 10–20 s and retry `sellerclaw-agent status`) or port `8001` being in use by another process.
- **Agent unreachable for `login` / `status`** — run `sellerclaw-agent start` and confirm nothing else is listening on `127.0.0.1:8001`.
- **Wrong cloud URL after switching environments** — run `docker compose down` **before** switching profiles so the container picks up the new `SELLERCLAW_API_URL`. Containers do not re-read env vars on simple restart.

### Cloud sign-in issues

- **Device flow never confirms** — check the browser tab actually signed in to the same cloud (`https://app.staging.sellerclaw.ai` for staging, etc.). The CLI polls for up to ~10 minutes; after that, rerun `sellerclaw-agent login`.
- **`401 invalid credentials` on email/password** — the cloud rejected the login; try again through the web, or use the device flow instead.
- **`502` during sign-in** — the agent could reach the cloud but the cloud returned a bad upstream response. Usually transient; retry after a minute.
- **Repeated `agent_session_invalidated`** — another agent instance is signed in with the same account. Either log out from the other device or accept that only the newest session survives (this is by design — see the [connection protocol](./connection-protocol.md#session-lifecycle)).

### One-command install specifics

- **`no matching manifest for linux/arm64`** — that version predates the arm64 builds. Install a newer one (`--version X.Y.Z`) or the latest release.
- **`sellerclaw-agent: command not found` right after installing** — `~/.local/bin` is not on your `PATH` (the installer warns about this). Add it, or call the full path `~/.local/bin/sellerclaw-agent`.
- **Nothing to ask on: `--yes` required** — the script was piped into `sh` with no terminal available (a provisioning script, CI). Re-run it with `--yes`: `curl -fsSL https://get.sellerclaw.ai/agent.sh | sh -s -- --yes`.
- **Upgrading** — `sellerclaw-agent update` re-runs the installer with the flags of the original install. The data volume is kept, so no second sign-in.

### Wiping local state

Installed with one command:

```bash
sellerclaw-agent uninstall --purge
curl -fsSL https://get.sellerclaw.ai/agent.sh | sh
```

From a checkout:

```bash
docker compose down
rm -rf data/agent_token.json data/secrets.json data/local_api_key data/edge_session.json
./setup.sh
```

Either way this clears the stored cloud agent token, the auto-generated local API key, and the current session ID, so the next start registers a fresh session.

## See also

- [Documentation index](./README.md)
- [Cloud connection protocol](./connection-protocol.md)
- [Agent manifest contract](./contracts/agent-manifest.md)
