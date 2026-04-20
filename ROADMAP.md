# Roadmap

This roadmap describes the public direction of **SellerClaw Agent** (`sellerclaw-agent`).

It is intended to show where the agent is heading, which areas matter most right now, and where community contributions can have the biggest impact.

This roadmap is **directional, not contractual**. Priorities may change as the agent evolves, community feedback grows, and the upstream OpenClaw runtime changes shape.

## Scope

This roadmap covers the **public `sellerclaw-agent` repository**: the CLI, the local FastAPI control-plane server, the bundle renderer, the admin UI, and the wire contracts the agent exposes.

It does not describe:

- internal operational plans of the SellerClaw cloud
- private cloud implementation details
- internal runbooks or infrastructure
- non-public enterprise-only functionality
- exact delivery dates

## Current Direction

The agent's job is to take a manifest from a control plane (the SellerClaw cloud or a self-hosted orchestrator), render an OpenClaw runtime bundle from it, and keep the runtime healthy. The long-term direction is to make that loop:

- easier to set up for a first-time user
- more predictable under real-world failures (Docker hiccups, token expiry, network flakiness)
- friendlier to alternative control planes, not just SellerClaw cloud
- transparent about what it is doing and why

In short: a small, boring, reliable runtime wrapper that can be trusted on long-lived user machines.

## Current Priorities

- improve the self-hosted first-run experience
- make the CLI onboarding (device flow, `.env` profiles) harder to misuse
- tighten the manifest contract and its validation
- improve runtime observability and diagnostics
- make the project easier to extend and contribute to

## Near-Term Priorities

### 1. Better onboarding and setup

The first priority is reducing friction for new users installing the agent on their own machine.

Focus areas include:

- clearer first-run messages from `sellerclaw-agent setup`
- better diagnostics when Docker is missing, stale, or misconfigured
- cleaner failure modes for expired or invalid credentials
- a friendlier admin UI empty state (no manifest, no cloud connection)
- better documentation for installation, `.env*` profiles, and local operation

### 2. Stronger manifest and bundle handling

The manifest is the agent's public contract. Making it robust matters more than making it feature-rich.

Focus areas include:

- stronger schema validation with helpful error messages
- better bundle rendering diagnostics (what changed, what was missing)
- cleaner handling of unknown / forward-compatible fields
- documentation parity between the schema, the example payload, and the Python models

### 3. More flexible cloud pairing

Today the agent is tested against the SellerClaw cloud, but the protocol itself is general.

Focus areas include:

- configurable `SELLERCLAW_API_URL` for third-party / self-hosted control planes
- better retry and backoff behavior during connection loss
- explicit handling of protocol version skew between agent and server
- clearer handling of `agent_suspended` and session replacement

### 4. Runtime observability

A long-lived local agent needs to be inspectable without SSH.

Focus areas include:

- more structured logs from the ping loop and bundle renderer
- richer `GET /openclaw/status` output
- easier log access from the admin UI
- better signals when the OpenClaw program misbehaves

### 5. Better contributor experience

The agent should be easy to understand and extend as a community project.

Focus areas include:

- clearer public documentation (CLI, protocol, contracts)
- stronger contributor guidance
- improved testing clarity and coverage
- cleaner extension paths for alternative hosting backends and control planes

## Longer-Term Direction

Over time, the agent is expected to grow in areas such as:

- support for additional control planes beyond SellerClaw cloud
- pluggable hosting backends for managed deployments
- richer local management surface (manifest editor, runtime controls)
- broader OpenClaw version compatibility
- stronger ecosystem support for community-built extensions
- continued product maturity for both operators and developers

## Good Areas for Contribution

If you want to contribute, the most helpful areas are often:

- onboarding and setup improvements
- documentation (CLI, connection protocol, manifest contract)
- manifest validation and error messages
- runtime reliability and diagnostics
- admin UI usability improvements
- testing and developer tooling
- small, focused bug fixes

## What This Roadmap Is Not

This roadmap is not:

- a release schedule
- a list of guaranteed deliverables
- a complete internal backlog
- a public dump of private product planning

It is a guide to the direction of the public agent project.

## Feedback and Contributions

Community feedback helps shape the direction of `sellerclaw-agent`.

If you want to contribute to one of the areas above, start with:

- [`README.md`](README.md)
- [`CONTRIBUTING.md`](CONTRIBUTING.md)
- [`docs/cli.md`](docs/cli.md)
- [`docs/connection-protocol.md`](docs/connection-protocol.md)

Thoughtful bug reports, documentation improvements, focused pull requests, and protocol work are all valuable ways to help move the project forward.
