# SellerClaw Agent

`sellerclaw-agent` is the local **edge agent** that runs on a user's machine and hosts an **OpenClaw** runtime for e-commerce workflows. OpenClaw is the underlying multi-agent framework that does the actual work (driving a browser, talking to suppliers, running LLM-backed tasks); `sellerclaw-agent` wraps it so that:

- a control plane (the SellerClaw cloud or any compatible orchestrator) can configure it over HTTP
- a non-technical user can install and sign in with a single command
- credentials, manifest state, and container lifecycle stay contained inside Docker

In practice the agent is a small, boring runtime wrapper: a **CLI** for onboarding, a **FastAPI control plane**, a **bundle renderer** that turns a JSON manifest into OpenClaw config, and a **Vue 3 admin UI** — all packaged into a single Docker image alongside the OpenClaw gateway itself.

> **Status:** working and open for experimentation. The CLI and wire contracts (manifest, connection protocol) are stable enough to build on; the admin UI and internal structure are still evolving. See the [roadmap](ROADMAP.md) for current priorities.

## What It Does

- **Pairs with a cloud** over HTTP polling — no inbound ports, no public IP required on the host.
- **Stores a manifest** sent by the control plane and renders an OpenClaw bundle from it on demand (`GET /bundle/archive`).
- **Runs OpenClaw** through `supervisord` in the same container (no `docker.sock` required).
- **Handles auth** — long-lived **agent-scoped** cloud token (`sca_…`); user JWT is not stored or used on the agent. Password and device-flow sign-in both obtain the same token type.
- **Exposes a local admin UI** at `http://localhost:5174/admin/` for viewing and editing the manifest in the browser.

## Who It Is For

- self-hosters who want to run an OpenClaw-based e-commerce agent on their own hardware
- developers building alternative control planes on top of the agent's wire contract
- teams integrating OpenClaw into their own operational tooling

## Quick Start

### Prerequisites

- **Docker** and **Docker Compose v2** (`docker compose version` must succeed).
- **Python 3.12+** (only for the CLI itself — services run inside Docker).
- [uv](https://docs.astral.sh/uv/) is recommended for managing the Python side.

### One-shot setup

From the `sellerclaw-agent/` directory:

```bash
./setup.sh
```

This checks for Docker, installs Python dependencies (via `uv` or `pip`), brings up the Docker stack, and runs the interactive sign-in — all in one step.

Alternatives:

```bash
uv run sellerclaw-agent setup    # if uv is already installed
make setup                       # via the project Makefile
```

After startup:

- Local FastAPI server: `http://localhost:8001`
- Admin UI: `http://localhost:5174/admin/`

### Environments

Each environment profile is a `.env.<name>` file in the repo root that controls which cloud the agent connects to. **Secrets** (for example `SELLERCLAW_LOCAL_API_KEY`, `AGENT_API_KEY`) belong in `secrets.env` (gitignored); copy `secrets.env.example` as a starting point.

| File | Cloud target |
|------|-------------|
| `.env.local` | Local development (`http://host.docker.internal:8000`) |
| `.env.staging` | Staging (`https://api.staging.sellerclaw.ai`) |
| `.env.production` | Production (`https://api.sellerclaw.ai`) |

Switch with `./setup.sh --env staging` or `export AGENT_ENV=staging`. See [`docs/cli.md`](docs/cli.md) for the full reference.

## CLI Commands

| Command | Description |
|---------|-------------|
| `setup` | Default — build + start stack, wait for the server, run interactive sign-in. |
| `start` | Start the stack only (`docker compose up -d --build`). |
| `stop` | Stop the stack (`docker compose down`). |
| `status` | Show cloud connection status (`GET /auth/status`). |
| `login` | Sign in to the cloud on an already-running agent. |
| `logout` | Clear stored cloud credentials. |

See [`docs/cli.md`](docs/cli.md) for full details and env var reference.

## Building the Runtime Image

The agent runs inside a combined Docker image that includes the OpenClaw runtime plus the agent server. For normal local use you don't need to build it yourself — `docker compose` will do it on first run. To build or publish a custom image (forks, self-hosted infra), see [Building the runtime image](docs/cli.md#building-the-runtime-image) in [`docs/cli.md`](docs/cli.md).

## Architecture

```text
┌─────────────────────────────────┐
│        Control plane            │   (SellerClaw cloud or compatible)
│                                 │
│  sends manifest, enqueues       │
│  start / stop / restart         │
└──────────────┬──────────────────┘
               │  HTTPS (outbound only)
               ▼
┌─────────────────────────────────┐
│        sellerclaw-agent         │
│                                 │
│  CLI           (onboarding)     │
│  FastAPI       (:8001 control)  │
│  Admin UI      (:5174 Vue 3)    │
│  Ping loop     (connect / ping) │
│  Bundle        (render OpenClaw)│
│  supervisord   (openclaw prog)  │
│                                 │
│  OpenClaw gateway (:7788)       │
│  KasmVNC browser                │
└─────────────────────────────────┘
```

The agent speaks two HTTP contracts:

- **Cloud-facing** — the [connection protocol](docs/connection-protocol.md): `connect` → `ping` loop → pull commands → report results.
- **Control-plane-facing** — the [manifest contract](docs/contracts/agent-manifest.md): `POST /manifest` stores a JSON payload, `GET /bundle/archive` returns the rendered bundle.

Both stay identical whether the agent is self-hosted or driven by a managed cloud.

## Repository Structure

```text
sellerclaw-agent/
├── sellerclaw_agent/       # Python package (CLI, server, cloud client, bundle renderer)
├── admin-ui/               # Vue 3 admin SPA (manifest viewer/editor, sign-in)
├── agent_resources/        # OpenClaw config templates used by the bundle renderer
├── runtime/                # Dockerfile for the combined OpenClaw + agent image
├── tests/                  # unit + contract tests
├── docs/                   # CLI, protocol, contracts, developer notes
├── docker-compose.yml      # local stack (server + admin-ui)
├── Makefile                # common tasks
├── pyproject.toml          # Python package metadata
└── setup.sh                # one-shot onboarding script
```

## Tech Stack

| Category | Technology |
|---|---|
| CLI | Python 3.12+, `rich`, `questionary`, `httpx` |
| Agent server | FastAPI, uvicorn, Pydantic v2 |
| Admin UI | Vue 3, Vite, TypeScript, axios |
| Runtime | OpenClaw, Node.js 20, KasmVNC, Playwright, supervisord |
| Tooling | Docker, Docker Compose, uv |
| Quality | pytest, ruff, pyright |

## Documentation

Start with the documentation index at [`docs/README.md`](docs/README.md). Key pages by audience:

**For operators / self-hosters:**

- [CLI reference](docs/cli.md) — install, env profiles, commands, troubleshooting.

**For developers integrating with the agent:**

- [Cloud connection protocol](docs/connection-protocol.md) — session lifecycle, commands, error handling.
- [Agent manifest contract](docs/contracts/agent-manifest.md) — wire format and versioning.
- [`agent-manifest-schema.json`](docs/contracts/agent-manifest-schema.json) — JSON Schema (source of truth).

**For contributors to the agent itself:**

- [Admin UI guide](docs/developer/admin-ui.md) — Vue 3 SPA structure, hot reload, API contract.
- [Contributing guide](CONTRIBUTING.md) — workflow, tests, documentation expectations.
- [Roadmap](ROADMAP.md) — public direction and good areas to contribute.

## Contributing

Contributions are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md) and the [roadmap](ROADMAP.md) for a sense of current priorities.

When contributing, please:

- keep changes compatible with the documented wire contract (manifest, protocol)
- avoid importing the upstream SellerClaw monolith — the agent must stay independently installable
- run `make lint` and `make test_unit` before opening a pull request
- update the relevant `docs/*` pages when public behavior changes

## Security

If you believe you have found a vulnerability, please do **not** open a public GitHub issue. See [SECURITY.md](SECURITY.md) for the private reporting process.

## About

SellerClaw Agent is developed by **SellerAI**, the team behind the broader SellerClaw product.

- SellerClaw: [sellerclaw.ai](https://sellerclaw.ai)
- SellerAI: [sellerai.com](https://sellerai.com)
- Contact: [hello@sellerai.com](mailto:hello@sellerai.com)

## License

`sellerclaw-agent` is available under the **Business Source License 1.1 (BSL 1.1)**. Self-hosting, modification, and non-production use are permitted. Offering the software as a hosted service to third parties is not allowed. The license converts to **Apache 2.0** on March 2, 2030.

See [LICENSE](LICENSE) for full terms.
