# Agent manifest contract

The **manifest** is the JSON payload that tells `sellerclaw-agent` how to build an OpenClaw runtime bundle for a single user. It is the public contract between the agent and whatever control plane feeds it: the SellerClaw cloud, a self-hosted orchestrator, or your own integration.

This document describes the wire format, how endpoints consume it, and what changes are considered breaking.

## Endpoints that consume the manifest

| Method and path on the agent | Role |
|------------------------------|------|
| `POST /manifest` | Body — JSON manifest (wire format below). The agent validates it (`SaveManifestRequest`), stores it in `ManifestStorage`, and returns `{ status, manifest_path, version }`. |
| `GET /manifest` | Returns the last saved manifest plus its version hash. `404` with `detail.code == manifest_not_found` when nothing has been saved yet. |
| `GET /bundle/archive` | Reads the last saved manifest, builds the OpenClaw config bundle (workspaces, `openclaw.json`, prompt resources), and streams it back as `application/gzip` (tar.gz). |

A typical orchestrator posts a manifest and then pulls the archive:

```text
POST /manifest     → stores the JSON, bumps the version
GET  /bundle/archive → 200 application/gzip, tar.gz of the rendered bundle
```

The archive is reproducible for a given manifest: the agent always rebuilds it from the stored JSON on demand. No bundle state is kept apart from the manifest itself.

## Auth

Requests to `POST /manifest`, `GET /manifest`, `GET /bundle/archive`, `GET /commands/history`, `POST /openclaw/*`, and the control-plane auth routes (`POST /auth/connect`, `GET /auth/status`, `POST /auth/disconnect`, `POST /auth/device/start`, `GET /auth/device/poll`) are protected by a **local control-plane** bearer token (not the cloud agent token):

```http
Authorization: Bearer <SELLERCLAW_LOCAL_API_KEY>
```

- **Meaning.** `SELLERCLAW_LOCAL_API_KEY` (or the auto-generated file `local_api_key` under `SELLERCLAW_DATA_DIR`) is the **incoming** secret for HTTP callers of the agent API on port `8001`. In development, keep this in `secrets.env` (not in `.env.*` profile files). The Admin UI bootstraps it via `GET /auth/local-bootstrap` (loopback only).
- **`AGENT_API_KEY` / `agent_token.json`.** These identify the agent to the **SellerClaw cloud** (`sca_…`). They are used for outbound `Authorization` on `/agent/connection/*`, chat SSE, etc. They are **not** accepted as the control-plane manifest key unless you deliberately set the same value in both places (not recommended).

Public routes that never require the local header: `GET /health`, `GET /auth/local-bootstrap` (loopback only), and the admin UI static mount when it is enabled. Do not place a reverse proxy in front of `/auth/local-bootstrap` without preserving the real client address as loopback; otherwise bootstrap may leak the local key to non-local callers.

## JSON Schema

Authoritative schema: [`agent-manifest-schema.json`](./agent-manifest-schema.json) (`$id: https://sellerclaw.ai/contracts/agent-manifest-v1.json`). It is the source of truth for the `POST /manifest` body.

The enums for `enabled_modules[]` and `connected_integrations[]` are kept in sync with the agent's internal module/integration identifiers. A client that needs to build manifests programmatically should validate against this schema before posting.

## Example payload

See [`agent-manifest.example.json`](./agent-manifest.example.json) for a minimal working example with all required fields.

Required top-level fields:

- `user_id` — UUID of the user the manifest belongs to (used to namespace OpenClaw state).
- `gateway_token` — token the OpenClaw gateway accepts for its own HTTP API.
- `hooks_token` — token the gateway accepts on `/hooks/...` endpoints.
- `litellm_base_url`, `litellm_api_key` — LLM gateway URL and the virtual key to use.
- `models` — at minimum a `complex` and a `simple` model spec (id, name, context window, max tokens; optionally `reasoning` and `input`).

Optional but common fields:

- `agent_api_base_path` — path segment (e.g. `/agent`) appended to the deployment-level `SELLERCLAW_API_URL` to form the **agent API base URL**. The agent derives `SELLERCLAW_AGENT_API_BASE_URL = SELLERCLAW_API_URL + agent_api_base_path` and uses that derived value both as the `{{api_base_url}}` prompt template variable and as the `baseUrl` for the `sellerclaw-web-search` OpenClaw plugin (so `POST {{SELLERCLAW_AGENT_API_BASE_URL}}/research/web-search` resolves correctly). Must start with `/` when non-empty.
- `template_variables` — string map substituted into prompt templates. The `api_base_url` variable is populated automatically by the agent and **must not** be shipped here (neither as `api_base_url` nor as the legacy `api_base_path`).
- `enabled_modules[]`, `connected_integrations[]` — control which OpenClaw agents and integrations are activated in the generated bundle.
- `global_browser_enabled`, `per_module_browser` — browser capability toggles.
- `telegram` — Telegram channel settings.
- `web_search` — only `{ "enabled": boolean }` is honored on save and in the stored manifest. Whether search is actually available (BYOK vs corporate keys) is decided on the SellerClaw monolith; the edge agent never receives web-search API keys in the manifest. When `enabled` is true, the rendered OpenClaw config uses the `sellerclaw-web-search` plugin with `baseUrl = SELLERCLAW_AGENT_API_BASE_URL` and injects the agent’s cloud bearer token (`agent_token.json` / `AGENT_API_KEY`) so it can call `POST {{SELLERCLAW_AGENT_API_BASE_URL}}/research/web-search`.
- `primary_channel`, `proxy_url` — delivery and networking options.
- `model_name_prefix` — advanced override: namespaces model IDs in the rendered OpenClaw config (e.g. `u:<prefix>/complex`).

Deployment-specific values that are **not** part of the manifest any more:

- The sellerclaw API host is read by the agent from `SELLERCLAW_API_URL` (used both as the OpenClaw plugin `apiBaseUrl` for `sellerclaw-ui` and to derive `SELLERCLAW_AGENT_API_BASE_URL` together with the manifest-supplied `agent_api_base_path`).
- Allowed CORS origins for the OpenClaw gateway UI come from `SELLERCLAW_WEB_URL` and `ADMIN_URL`.

### Bundle archive and secrets

`GET /bundle/archive` returns `openclaw/openclaw.json` built from the saved manifest. When web search is enabled, that JSON includes `plugins.entries["sellerclaw-web-search"].config.webSearch.authToken` — the same **outgoing** cloud agent bearer (`sca_…`) as in `agent_token.json` / `AGENT_API_KEY`. Treat the archive like credential material: only fetch it over a trusted path, and rely on the **local** control-plane bearer (`SELLERCLAW_LOCAL_API_KEY`) to protect the endpoint.

## Versioning

- **Additive changes** — adding new optional top-level fields or properties (with `additionalProperties: true` at the root) is backward compatible. Agents that don't know a new field ignore it.
- **Breaking changes** — renaming or removing fields, narrowing an enum, or changing the type of an existing field is breaking. It requires a new major version of the contract with a fresh `$id`, and coordinated updates on every client.

The `web_search` object was narrowed to an `enabled` flag only (no `provider` / `api_key` in the wire format). During monolith rollout, `POST /manifest` may still receive legacy `web_search` keys; they are **ignored** by the agent and are **not** written back to disk. The JSON Schema allows extra properties under `web_search` so validators do not reject transitional payloads.

One field is specifically part of the contract and must be preserved end-to-end:

- `model_name_prefix` — the agent must pass it to `BundleBuilder` so the OpenClaw config namespaces model IDs correctly.

## Control plane vs OpenClaw gateway

The agent's HTTP API (FastAPI) — including `/manifest` and `/bundle/archive` — lives on the **control plane** port (default `8001`). The OpenClaw gateway UI runs on a **different** port (default `7788`) inside the same container, managed by `supervisord`.

Orchestrators should always talk to the control-plane URL, never to the gateway directly. The control plane is what understands the manifest; the gateway only consumes the rendered bundle from disk.

## See also

- [Documentation index](../README.md)
- [CLI reference](../cli.md)
- [Agent manifest JSON Schema](./agent-manifest-schema.json)
- [Example manifest](./agent-manifest.example.json)
