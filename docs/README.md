# SellerClaw Agent Documentation

This is the public documentation for **SellerClaw Agent** — the self-hosted runtime for [SellerClaw](https://sellerclaw.ai). It lets a SellerClaw user run the OpenClaw agent on their own machine instead of using the SellerClaw Cloud runtime, while continuing to work in the regular web admin panel at `sellerclaw.ai`.

For a product overview, see the top-level [`README`](../README.md). The pages here are grouped by what you are trying to do.

## Install and run the agent

Start here if you want to install SellerClaw Agent on your own machine and pair it with your SellerClaw account.

```bash
curl -fsSL https://get.sellerclaw.ai/agent.sh | sh
```

- **[CLI — installation and usage](./cli.md)** — the one-command install and the `sellerclaw-agent` command it gives you, the contributor path that builds the image from source, `.env.local` / `.env.staging` / `.env.production` profiles and `secrets.env`, `setup` / `login` / `status`, and the most common first-run failures.

## Integrate with the agent

Start here if you are working on the SellerClaw Cloud side of the connection, or building a compatible orchestrator on top of the public wire format.

- **[Cloud connection protocol](./connection-protocol.md)** — how the agent opens a session, heartbeats, pulls commands (`start` / `stop` / `restart` / `disconnect`), reports results, and recovers from errors.
- **[Agent manifest contract](./contracts/agent-manifest.md)** — wire format of `POST /manifest`, how `GET /bundle/archive` renders the OpenClaw config, auth, and versioning rules.
- [`agent-manifest-schema.json`](./contracts/agent-manifest-schema.json) — JSON Schema (source of truth) for the manifest; validate against this before posting.
- [`agent-manifest.example.json`](./contracts/agent-manifest.example.json) — minimal working example payload.

## Contribute to the agent

Start here if you are changing the agent's code.

See the top-level [`CONTRIBUTING.md`](../CONTRIBUTING.md) for the contribution workflow and [`ROADMAP.md`](../ROADMAP.md) for current priorities.

## Quick reference

- **Local control plane (agent HTTP API):** `http://localhost:8001` — fixed, published from the container in `docker-compose.yml`
- **OpenClaw gateway:** `:7788` inside the container, supervised alongside the agent server
- **On-disk state:** `agent_token.json`, `local_api_key`, and `edge_session.json` under `SELLERCLAW_DATA_DIR` (`/data` inside the container — the `sellerclaw-agent-data` volume for a one-command install, the bind-mounted `./data` directory in a checkout)
