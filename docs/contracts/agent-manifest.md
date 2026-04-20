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

Requests to `POST /manifest`, `GET /manifest`, `GET /bundle/archive`, and `POST /openclaw/*` are protected by a bearer token:

```http
Authorization: Bearer <AGENT_API_KEY>
```

- **Self-hosted.** Set `AGENT_API_KEY` in the agent environment (see the `.env*` files). The orchestrator must send the same value.
- **Managed (SellerClaw cloud).** The cloud stores a per-user token and passes it as the `Authorization` header when it provisions or pulls the bundle. The value is opaque from the agent's point of view.

Public routes that never require the header: `/health`, `/auth/*`, `/commands/history`, and the admin UI static mount when it is enabled.

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
- `webhook_api_base_url` — base URL the agent hits for outbound webhooks.

Optional but common fields:

- `enabled_modules[]`, `connected_integrations[]` — control which OpenClaw agents and integrations are activated in the generated bundle.
- `global_browser_enabled`, `per_module_browser` — browser capability toggles.
- `telegram`, `web_search` — integration-specific settings.
- `primary_channel`, `proxy_url` — delivery and networking options.
- `model_name_prefix`, `extra_allowed_origins` — advanced overrides for the rendered OpenClaw config (model namespacing and CORS).

## Versioning

- **Additive changes** — adding new optional top-level fields or properties (with `additionalProperties: true` at the root) is backward compatible. Agents that don't know a new field ignore it.
- **Breaking changes** — renaming or removing fields, narrowing an enum, or changing the type of an existing field is breaking. It requires a new major version of the contract with a fresh `$id`, and coordinated updates on every client.

Two fields are specifically part of the contract and must be preserved end-to-end:

- `model_name_prefix` — the agent must pass it to `BundleBuilder` so the OpenClaw config namespaces model IDs correctly.
- `extra_allowed_origins` — the agent must thread these origins into the OpenClaw gateway CORS list.

## Control plane vs OpenClaw gateway

The agent's HTTP API (FastAPI) — including `/manifest` and `/bundle/archive` — lives on the **control plane** port (default `8001`). The OpenClaw gateway UI runs on a **different** port (default `7788`) inside the same container, managed by `supervisord`.

Orchestrators should always talk to the control-plane URL, never to the gateway directly. The control plane is what understands the manifest; the gateway only consumes the rendered bundle from disk.

## See also

- [Documentation index](../README.md)
- [CLI reference](../cli.md)
- [Agent manifest JSON Schema](./agent-manifest-schema.json)
- [Example manifest](./agent-manifest.example.json)
