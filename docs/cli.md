# `sellerclaw-agent` CLI

Command-line tool for the local edge agent: it brings up the Docker stack, connects the agent to the SellerClaw cloud, and prints the Admin UI URL. **All auth traffic goes to the local Agent Server only** (not directly to the cloud) — your credentials never leave your machine without passing through the agent process first.

This page covers installation, environments, every CLI command, and common failure modes. If you only want to get started quickly, run `./setup.sh` from the `sellerclaw-agent/` directory and come back here when something surprises you.

## Requirements

- **Docker** and **Docker Compose v2** (`docker compose version` must succeed).
- **Python 3.12+** (only for running the CLI itself; the agent services run inside Docker).
- **Combined runtime image** — `docker compose` builds a single image (OpenClaw browser stack + SellerClaw agent) from `runtime/Dockerfile` target `staging`.

For normal local use the image is built automatically on first run. If you want to pre-build or publish your own copy, see [Building the runtime image](#building-the-runtime-image) below.

Optional: set `OPENCLAW_RUNTIME_IMAGE` to a tag for display in `GET /openclaw/status`.

## Quick start

One command from the `sellerclaw-agent/` directory:

```bash
cd sellerclaw-agent
./setup.sh
```

This script checks for Docker, installs Python dependencies (via `uv` or `pip`), brings up the Docker stack, and starts the interactive sign-in — all in a single step.

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

`docker compose` and the CLI pass `--env-file` for the profile and, when the file exists, `--env-file secrets.env`. If `secrets.env` is missing, only the profile file is used (the local API key is then auto-generated under `data/local_api_key` unless you set the variable another way).

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

When `AGENT_ENV` is not set, `.env.local` is used by default (see `setup.sh`).

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
| `ADMIN_URL` | Admin UI URL — used as the CORS origin for the agent HTTP API | Profile `.env.*` |
| `SELLERCLAW_LOCAL_API_KEY` | **Incoming** Bearer for control-plane routes (`/manifest`, `/auth/*` except bootstrap, `/bundle/archive`, `/openclaw/*`, `/commands/history`, …) on port `8001` | `secrets.env` or unset (auto-generated under `SELLERCLAW_DATA_DIR/local_api_key`) |
| `AGENT_API_KEY` | **Outgoing** Bearer for the SellerClaw cloud (`/agent/connection/*`, chat SSE, etc.) — same role as the token in `agent_token.json` | `secrets.env` or sign-in |
| `SELLERCLAW_DATA_DIR` | Where the agent stores `agent_token.json`, `local_api_key`, `edge_session.json`, manifest | `/data` (inside the container) |
| `SELLERCLAW_EDGE_PING` | Enable the background ping loop (cloud mode) | `1` |
| `SELLERCLAW_AGENT_IMAGE` | Pin a specific runtime image tag instead of building locally | *(unset)* |

The agent server always listens on port `8001` inside the container. By default compose publishes it as **`127.0.0.1:8001`** on the host (loopback only); the CLI reaches it at `http://127.0.0.1:8001`.

See the [cloud connection protocol](./connection-protocol.md) for how the ping loop uses `SELLERCLAW_API_URL` and `SELLERCLAW_DATA_DIR`.

## Commands

| Command | Description |
|---------|-------------|
| `setup` | **Default** when no argument is given: `docker compose up -d --build`, wait for `GET /health`, interactive cloud sign-in, print Admin UI URL. |
| `start` | Start the stack only: `docker compose up -d --build` in the agent directory. |
| `stop` | Stop the stack: `docker compose down`. |
| `status` | Show whether the agent is connected to the cloud (`GET /auth/status`). |
| `login` | Sign in to the cloud (server must be running): up to 15 s wait for the agent, then the same interactive flow as `setup`. |
| `logout` | Clear stored cloud credentials on the agent (`POST /auth/disconnect`). |
| `help` | Short help. Same idea: `-h`, `--help`, `help`. |

Unknown command: exit code `2`.

## Signing in to the cloud (interactive)

For `setup` or `login` you can choose:

1. **Email and password** — sent to the local agent at `POST /auth/connect`; the agent talks to the cloud.
2. **Browser (device flow)** — the agent requests codes (`POST /auth/device/start`); the terminal shows the user code and verification link; the CLI polls `GET /auth/device/poll?device_code=...` until success or timeout. In the browser, sign in to SellerClaw and approve the device.

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

## Troubleshooting

### First-run failures

- **`Docker Compose v2 not found`** — install the Compose plugin and verify `docker compose version`. On Linux the plugin ships as the `docker-compose-plugin` package; on macOS it is bundled with Docker Desktop.
- **`permission denied while trying to connect to the Docker daemon`** — add your user to the `docker` group (`sudo usermod -aG docker $USER`) and start a new shell, or run `docker` with `sudo` temporarily.
- **First `setup` takes a very long time** — the runtime image is large (~1.5 GB). The initial `docker compose up --build` pulls the OpenClaw base image and installs Chromium, KasmVNC, supervisord, and Playwright. Subsequent runs are fast.

### After setup

- **Timeout waiting for the agent after setup** (`GET /health` during `setup`) — inspect logs with `docker compose logs` in `sellerclaw-agent/`. The most common causes are the server still booting (wait another 10–20 s and retry `sellerclaw-agent status`) or port `8001` being in use by another process.
- **Agent unreachable for `login` / `status`** — run `sellerclaw-agent start` and confirm nothing else is listening on `127.0.0.1:8001`.
- **Wrong cloud URL after switching environments** — run `docker compose down` **before** switching profiles so the container picks up the new `SELLERCLAW_API_URL`. Containers do not re-read env vars on simple restart.
- **Admin UI at `http://localhost:5174/admin/` does not load** — confirm the `admin-ui` service is running (`docker compose ps`). If you recently added an npm dependency, rebuild with `docker compose build admin-ui && docker compose up -d admin-ui`. See [`developer/admin-ui.md`](./developer/admin-ui.md) for details.

### Cloud sign-in issues

- **Device flow never confirms** — check the browser tab actually signed in to the same cloud (`https://app.staging.sellerclaw.ai` for staging, etc.). The CLI polls for up to ~10 minutes; after that, rerun `sellerclaw-agent login`.
- **`401 invalid credentials` on email/password** — the cloud rejected the login; try again through the web, or use the device flow instead.
- **`502` during sign-in** — the agent could reach the cloud but the cloud returned a bad upstream response. Usually transient; retry after a minute.
- **Repeated `agent_session_invalidated`** — another agent instance is signed in with the same account. Either log out from the other device or accept that only the newest session survives (this is by design — see the [connection protocol](./connection-protocol.md#session-lifecycle)).

### Wiping local state

If you want to start completely over:

```bash
docker compose down
rm -rf data/agent_token.json data/local_api_key data/edge_session.json
./setup.sh
```

This clears the stored cloud agent token, the auto-generated local API key, and the current session ID so the next `setup` registers a fresh session.

## See also

- [Documentation index](./README.md)
- [Cloud connection protocol](./connection-protocol.md)
- [Agent manifest contract](./contracts/agent-manifest.md)
- [Admin UI](./developer/admin-ui.md)
