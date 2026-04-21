# Security Policy

Thank you for helping keep **SellerClaw Agent** and its users safe.

This document explains how to report security issues responsibly in the public `sellerclaw-agent` repository.

## Supported Scope

This policy applies to the public **`sellerclaw-agent`** repository and its documentation.

It covers security issues related to areas such as:

- the CLI (`sellerclaw-agent setup` / `login` / `logout`, device flow)
- the local FastAPI control-plane server (`/manifest`, `/auth/*`, `/openclaw/*`)
- authentication and session handling against a SellerClaw (or compatible) cloud
- credential storage on disk (`agent_token.json`, `local_api_key`, `edge_session.json`)
- the admin UI (Vue 3 SPA under `admin-ui/`)
- the bundle renderer and the manifest wire contract
- the combined OpenClaw + agent Docker image defined in `runtime/Dockerfile`
- unsafe defaults in the public self-hosted experience
- vulnerabilities introduced through public code changes

It does not cover the upstream SellerClaw cloud, private internal systems, or non-public infrastructure that is not part of this repository.

## How to Report a Security Issue

Please **do not open a public GitHub issue** for suspected security vulnerabilities.

Instead, report security issues privately by email:

- **hello@sellerai.com**

Use a subject line such as:

- `Security report: SellerClaw Agent`

## What to Include

To help us investigate quickly, please include as much of the following as possible:

- a clear description of the issue
- the affected area or file (e.g. `sellerclaw_agent/server/app.py`, `admin-ui/src/...`)
- steps to reproduce the problem
- the expected behavior
- the actual behavior
- any proof of concept, logs, or screenshots that help explain the issue
- the version, commit, or tag you tested (including the base OpenClaw image tag if relevant)
- any suggested mitigation if you have one

If the issue depends on a specific configuration (environment profile, `.env*` values, docker-compose overrides), please describe that configuration clearly.

## Responsible Disclosure Expectations

When reporting a security issue, please:

- give us reasonable time to investigate and respond before public disclosure
- avoid sharing exploit details publicly before the issue is addressed
- avoid accessing data that does not belong to you
- avoid actions that could harm other users, systems, or services
- avoid destructive testing against systems you do not own or control

We appreciate good-faith research and responsible disclosure.

## What to Avoid Sending Publicly

Please do not publish the following in public issues or pull requests:

- secrets or tokens (including `AGENT_API_KEY`, `hooks_token`, `gateway_token`, LiteLLM virtual keys)
- long-lived agent tokens (`sca_…`) from `agent_token.json` or `AGENT_API_KEY`
- the local control-plane secret (`SELLERCLAW_LOCAL_API_KEY` / `local_api_key` file)
- contents of `edge_session.json`
- integration credentials (Shopify, supplier, Telegram bot tokens, etc.)
- exploit payloads that enable trivial abuse
- sensitive local environment files (`.env.local`, `.env.production`)
- personal data

If you accidentally include sensitive material in a public report, contact us as quickly as possible by email.

## Security Fixes

If a reported issue is confirmed, the fix process may include:

- a private investigation
- a patch in the public repository when appropriate
- a rebuild and re-tag of the runtime image
- documentation or configuration updates
- coordinated disclosure timing if needed

Not every report will result in a public write-up, but all good-faith reports are valuable.

## Scope Boundaries

Some issues may fall outside the scope of this public repository (for example the SellerClaw cloud itself or internal deployment systems).

Examples that may be out of scope here include:

- private cloud infrastructure
- internal deployment systems
- internal runbooks
- non-public enterprise-only implementation details
- vulnerabilities in the upstream OpenClaw runtime (report those upstream)
- vulnerabilities that require access to private systems not present in this repository

If your report touches both this repository and a broader product concern, please still send it privately by email and explain what you observed.

## Safe Local Operation

`sellerclaw-agent` is designed to run as a local self-hosted stack on a user's own machine. Even so, operators and contributors should follow basic security practices such as:

- keeping `AGENT_API_KEY` and cloud tokens out of git
- never committing `data/agent_token.json`, `data/local_api_key`, or `data/edge_session.json`
- using strong local credentials for cloud accounts
- avoiding exposing the agent's control-plane port (`8001` by default) to the public network (compose binds it to `127.0.0.1` by default)
- understanding that `/auth/local-bootstrap` is intentionally loopback-only (`127.0.0.1`, `::1`, other `127.*`, and IPv4-mapped `::ffff:127.*`): anyone who can open `8001` from non-loopback addresses could otherwise obtain the local API key; do not put this path behind a reverse proxy that hides the real client address
- keeping `SELLERCLAW_LOCAL_API_KEY` and other secrets in `secrets.env` (gitignored), not in committed `.env.*` profile files
- pinning a known runtime image tag (`SELLERCLAW_AGENT_IMAGE`) instead of tracking `latest` in production
- reviewing integration credentials carefully before adding them to a manifest
- keeping Docker, Compose, and local tooling reasonably up to date

## Thank You

Security reports help improve SellerClaw Agent for everyone.

If you believe you have found a vulnerability, please report it privately at **hello@sellerai.com**.
