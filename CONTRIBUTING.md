# Contributing to SellerClaw Agent

Thank you for contributing to **SellerClaw Agent**.

`sellerclaw-agent` is the local edge agent that wraps the OpenClaw runtime: it exposes a small FastAPI control plane, ships a CLI for onboarding (`sellerclaw-agent setup` / `login`), and pairs with a SellerClaw (or compatible) cloud over HTTP polling. Contributions are welcome from developers who want to improve the agent's CLI, runtime behaviour, manifest handling, admin UI, or tooling around it.

This guide explains how to contribute effectively.

## Before You Start

Before making changes, please make sure you understand the role of this repository.

Contributions should:

- improve or extend the agent in ways that fit the documented architecture
- remain compatible with both self-hosted and managed deployment modes
- avoid depending on private infrastructure or cloud-only internals
- keep documentation accurate when behavior changes

If you are looking for agent-specific context, start with:

- [`docs/README.md`](docs/README.md) — documentation index
- [`docs/cli.md`](docs/cli.md) — CLI installation and usage
- [`docs/connection-protocol.md`](docs/connection-protocol.md) — cloud pairing protocol
- [`docs/contracts/agent-manifest.md`](docs/contracts/agent-manifest.md) — manifest wire contract
- [`docs/developer/admin-ui.md`](docs/developer/admin-ui.md) — Vue 3 admin UI

## What Kinds of Contributions Are Welcome

We welcome contributions such as:

- bug fixes in the CLI, FastAPI server, or bundle renderer
- improvements to the manifest validation / schema
- admin UI usability and clarity improvements
- better error messages and diagnostics
- additional hosting-mode flexibility (env profiles, custom cloud URLs)
- test coverage improvements
- documentation improvements, especially around first-run setup
- refactoring that improves clarity, maintainability, or extensibility

Good contributions usually make the agent easier to run, easier to understand, or more robust against real-world failures (Docker hiccups, token refresh, manifest drift, etc.).

## Before Opening an Issue or Pull Request

Please do a quick check first:

- confirm that the problem or idea fits this repository (not the upstream SellerClaw product)
- search existing issues or pull requests
- make sure you can describe the problem, motivation, and expected behavior clearly

For larger changes, it is often better to describe the proposal first before investing heavily in implementation.

## Development Workflow

A typical contribution flow looks like this:

1. Fork the repository.
2. Create a focused branch.
3. Run the agent locally.
4. Make the smallest coherent change that solves the problem.
5. Run the relevant checks (`make lint`, `make test_unit`).
6. Update documentation if public behavior changed.
7. Open a pull request with a clear explanation.

Keep pull requests focused. Small and understandable changes are much easier to review than large mixed-purpose PRs.

## Local Setup

Clone your fork and bring up the stack:

```bash
git clone https://github.com/<your-username>/sellerclaw-agent.git
cd sellerclaw-agent
git remote add upstream https://github.com/sellerai-com/sellerclaw-agent.git
./setup.sh
```

`setup.sh` checks for Docker, installs Python dependencies (via `uv` or `pip`), brings up the Docker stack, and starts the interactive sign-in.

For iterating on the agent server and admin UI with hot reload:

```bash
make up          # docker compose up --build (server + admin-ui)
```

For the server only (faster cycle when you are not touching the UI):

```bash
make up-server
```

Building or publishing a runtime image from a fork is described under [Building the runtime image](docs/cli.md#building-the-runtime-image) in [`docs/cli.md`](docs/cli.md).

See [`docs/cli.md`](docs/cli.md) for environment profiles (`.env`, `.env.staging`, `.env.production`, `.env.local`) and [`docs/developer/admin-ui.md`](docs/developer/admin-ui.md) for the admin UI setup.

## Code Expectations

When contributing code, please aim for changes that are:

- clear
- minimal
- consistent with the existing module structure
- easy for future contributors to understand

A few practical guidelines:

- keep changes close to the feature area they belong to
- avoid unrelated refactors in the same pull request
- do not import the upstream SellerClaw monolith: the agent must stay independently installable
- keep the control-plane API (`/manifest`, `/auth/*`, `/openclaw/*`) compatible with the documented wire contract unless you are explicitly bumping the protocol version
- prefer maintainable design over quick one-off patches

## Testing Expectations

Run the checks that match the scope of your change:

```bash
make lint        # ruff + pyright
make test_unit   # unit tests (no Docker needed)
make test        # all tests including those that require the stack
```

If your change affects behavior, make sure there is appropriate test coverage for that behavior. In particular:

- manifest parsing / schema changes must be covered in `tests/unit/contracts/`
- server route changes must be covered in `tests/unit/server/`
- bundle rendering changes must be covered in `tests/unit/bundle/`

## Documentation Expectations

If your change affects public behavior, update the documentation in the same pull request.

This includes changes that affect:

- CLI commands, flags, or output
- environment variables or `.env*` profiles
- the manifest wire format or schema
- control-plane HTTP routes
- the cloud connection protocol
- admin UI behavior

At minimum, keep the relevant docs in sync:

- [`docs/cli.md`](docs/cli.md)
- [`docs/connection-protocol.md`](docs/connection-protocol.md)
- [`docs/contracts/agent-manifest.md`](docs/contracts/agent-manifest.md)
- [`docs/contracts/agent-manifest-schema.json`](docs/contracts/agent-manifest-schema.json)
- [`docs/developer/admin-ui.md`](docs/developer/admin-ui.md)

## Pull Request Guidelines

A good pull request usually includes:

- a clear title
- a short explanation of the problem
- a short explanation of the solution
- notes about any important tradeoffs or limitations
- test and documentation updates when relevant

Try to keep the PR easy to review.

Helpful habits:

- prefer one main purpose per PR
- avoid mixing cleanup with feature work unless necessary
- explain why the change matters, not just what changed
- mention any setup steps reviewers should know about

## Security and Secrets

Please do not commit:

- secrets
- private credentials
- JWT access or refresh tokens
- local environment files (`.env.local`, `data/credentials.json`, `data/edge_session.json`)
- internal-only operational details that do not belong in the public repository

If you discover a potential security issue, do not open a public exploit-style issue with sensitive details. See [SECURITY.md](SECURITY.md) for the preferred reporting process.

## Scope Boundaries

Some changes may be out of scope for contribution if they depend on:

- private cloud infrastructure
- server-side internals of the SellerClaw monolith
- internal operational runbooks or deployment pipelines
- non-public enterprise-specific implementation details

When in doubt, keep the contribution focused on the agent's public self-hosted behavior and the documented wire contract.

## Good First Contributions

If you are contributing for the first time, good places to start include:

- documentation fixes (especially `docs/cli.md` and the onboarding flow)
- small CLI polish (clearer errors, better help text)
- admin UI clarity improvements
- test coverage improvements
- small bug fixes
- cleanup that improves readability without changing behavior

These are often the fastest way to learn the codebase and contribute something useful.

## Thank You

SellerClaw Agent improves through public iteration and thoughtful contributions.

Whether you are fixing a typo, improving an integration, refining the CLI, or extending the runtime behavior, your contribution helps make the agent stronger for everyone.
