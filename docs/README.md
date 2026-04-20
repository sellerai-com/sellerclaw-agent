# SellerClaw Agent Documentation

Documentation for the `sellerclaw-agent` package — the local edge agent that wraps the OpenClaw runtime, exposes a local control-plane API, and pairs with a SellerClaw (or compatible) cloud.

The pages below are grouped by audience so you can jump straight to what you need.

## For operators and self-hosters

Start here if you want to install the agent, connect it to a cloud, and run it on your own machine.

- [**CLI — installation and usage**](./cli.md) — install `sellerclaw-agent`, understand the `.env*` profiles, run `setup` / `login` / `status`, build the runtime image, and troubleshoot the most common first-run failures.

## For developers integrating with the agent

Start here if you are building a control plane or another system that drives the agent over HTTP.

- [**Cloud connection protocol**](./connection-protocol.md) — how the agent opens a session, heartbeats, pulls commands (`start` / `stop` / `restart` / `disconnect`), reports results, and recovers from errors.
- [**Agent manifest contract**](./contracts/agent-manifest.md) — wire format of `POST /manifest`, how `GET /bundle/archive` renders the OpenClaw config, auth, and versioning rules.
- [`agent-manifest-schema.json`](./contracts/agent-manifest-schema.json) — JSON Schema (source of truth) for the manifest; validate against this before posting.
- [`agent-manifest.example.json`](./contracts/agent-manifest.example.json) — minimal working example payload.

## For contributors to the agent itself

Start here if you are changing the agent's code.

- [**Admin UI**](./developer/admin-ui.md) — the Vue 3 SPA for viewing and editing the manifest: structure, hot reload, API contract used by the UI, and backend coverage.

See also the top-level [`CONTRIBUTING.md`](../CONTRIBUTING.md) for the contribution workflow and [`ROADMAP.md`](../ROADMAP.md) for current priorities.

## Quick reference

- **Control-plane port (agent HTTP API):** `8001` (fixed; published from the container in `docker-compose.yml`).
- **OpenClaw gateway port:** `7788` by default — runs as a separate process inside the same container, managed by `supervisord`.
- **Admin UI port (dev):** `5174`.
- **On-disk state:** `credentials.json` and `edge_session.json` under `SELLERCLAW_DATA_DIR` (default `/data` inside the container, bind-mounted to `./data` on the host).
