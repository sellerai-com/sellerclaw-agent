# Agent manifest contract

The **manifest** is the JSON payload that tells `sellerclaw-agent` how to build an OpenClaw runtime bundle for a single user. It is the public contract between the agent and whatever control plane feeds it: the SellerClaw cloud, a self-hosted orchestrator, or your own integration.

This document describes the wire format, how endpoints consume it, and what changes are considered breaking.

## Endpoints that consume the manifest

| Method and path on the agent | Role |
|------------------------------|------|
| `POST /manifest` | Body — JSON manifest (wire format below). The agent validates it (`SaveManifestRequest`), stores it in `ManifestStorage`, and returns `{ status, manifest_path, version }`. |
| `GET /manifest` | Returns the last saved manifest plus its version hash. `404` with `detail.code == manifest_not_found` when nothing has been saved yet. |
| `GET /bundle/archive` | Reads the last saved manifest, builds the OpenClaw config bundle (`openclaw.json` plus the per-agent workspace files) directly from the manifest payload, and streams it back as `application/gzip` (tar.gz). |

A typical orchestrator posts a manifest and then pulls the archive:

```text
POST /manifest     → stores the JSON, bumps the version
GET  /bundle/archive → 200 application/gzip, tar.gz of the rendered bundle
```

The archive is reproducible for a given manifest: the agent always rebuilds it from the stored JSON on demand. No bundle state is kept apart from the manifest itself. The agent does **not** read any on-disk prompt, skill, or config templates — every agent's instructions, skills, and per-agent flags arrive already rendered in the manifest, and the agent only packs them into the bundle (see [Agents block](#agents-block)).

## Auth

Requests to `POST /manifest`, `GET /manifest`, `GET /bundle/archive`, `GET /commands/history`, `POST /openclaw/*`, and the control-plane auth routes (`POST /auth/connect`, `GET /auth/status`, `POST /auth/disconnect`, `POST /auth/device/start`, `GET /auth/device/poll`) are protected by a **local control-plane** bearer token (not the cloud agent token):

```http
Authorization: Bearer <SELLERCLAW_LOCAL_API_KEY>
```

- **Meaning.** `SELLERCLAW_LOCAL_API_KEY` (or the auto-generated entry in `secrets.json` under `SELLERCLAW_DATA_DIR`, after one-time migration from legacy `local_api_key`) is the **incoming** secret for HTTP callers of the agent API on port `8001`. In development, keep this in `secrets.env` (not in `.env.*` profile files). The Admin UI bootstraps it via `GET /auth/local-bootstrap` (loopback only).
- **`AGENT_API_KEY` / `agent_token.json`.** These identify the agent to the **SellerClaw cloud** (`sca_…`). They are used for outbound `Authorization` on `/agent/connection/*`, chat SSE, etc. They are **not** accepted as the control-plane manifest key unless you deliberately set the same value in both places (not recommended).

Public routes that never require the local header: `GET /health`, `GET /auth/local-bootstrap` (loopback only), and the admin UI static mount when it is enabled. Do not place a reverse proxy in front of `/auth/local-bootstrap` without preserving the real client address as loopback; otherwise bootstrap may leak the local key to non-local callers.

## JSON Schema

Authoritative schema: [`agent-manifest-schema.json`](./agent-manifest-schema.json) (`$id: https://sellerclaw.ai/contracts/agent-manifest-v2.json`). It is the source of truth for the `POST /manifest` body.

Every model ref (`{ group, model }`) must resolve to a group declared under `llm.groups` — and, when that group lists model ids, to one of those ids. The agent rejects manifests whose refs do not resolve. A client that needs to build manifests programmatically should validate against this schema before posting.

## Example payload

See [`agent-manifest.example.json`](./agent-manifest.example.json) for a minimal working example with all required fields.

The manifest is **generic and fully pre-rendered**: the control plane assembles each agent's instructions, skills, and per-agent flags, and the agent derives the OpenClaw config and workspaces from the payload alone. There is no on-disk module registry, no prompt/skill template rendering, and no per-integration config assembly on the agent side.

Required top-level fields (per the schema): `user_id`, `llm`, `agents`. The agent additionally expects `agent_api_base_path` and a `channels` block.

- `user_id` — UUID of the user the manifest belongs to (used to namespace OpenClaw state).
- `llm` — LLM routing block (see [LLM block](#llm-block)).
- `agents` — the agents block: defaults plus a `main_agent` (entry point) and an ordered `subagents[]` list, each carrying pre-rendered content (see [Agents block](#agents-block)).

**Local OpenClaw tokens** (`gateway_token`, `hooks_token` for the gateway HTTP API and `/hooks/...`) are **not** part of the manifest or the `POST /manifest` JSON schema. They are generated once under `SELLERCLAW_DATA_DIR/secrets.json` (mode `0600`) or overridden per key via `SELLERCLAW_GATEWAY_TOKEN` / `SELLERCLAW_HOOKS_TOKEN`. If an old `manifest.json` on disk still contains these keys, `GET /manifest` strips them from the response; rewrite the file on the next `POST /manifest` from the control plane to drop them from disk.

Other top-level fields:

- `agent_api_base_path` — path segment (e.g. `/agent`) appended to the deployment-level `SELLERCLAW_API_URL` to form the **agent API base URL**. The agent derives `SELLERCLAW_AGENT_API_BASE_URL = SELLERCLAW_API_URL + agent_api_base_path` and uses it as the `baseUrl` for the `sellerclaw-web-search` OpenClaw plugin (so `POST {{SELLERCLAW_AGENT_API_BASE_URL}}/research/web-search` resolves correctly). Must start with `/` when non-empty.
- `channels` — delivery wiring (see [Channels and toggles](#channels-and-toggles)).
- `web_search` — only `{ "enabled": boolean }` is honored on save and in the stored manifest. Whether search is actually available (BYOK vs corporate keys) is decided on the SellerClaw monolith; the edge agent never receives web-search API keys in the manifest. When `enabled` is true, the OpenClaw config uses the `sellerclaw-web-search` plugin with `baseUrl = SELLERCLAW_AGENT_API_BASE_URL` and injects the agent's cloud bearer token (`agent_token.json` / `AGENT_API_KEY`) so it can call `POST {{SELLERCLAW_AGENT_API_BASE_URL}}/research/web-search`.
- `proxy_url` — optional outbound proxy for the runtime.
- `cron`, `web_fetch` — `{ "enabled": boolean }` toggles; both default to enabled when omitted.

### LLM block

`llm` carries all model routing. It has no flat gateway URL or key — those live per provider group:

- `groups` — a map of provider name → `{ base_url, api_key, model_name_prefix, models[], api? }`. `model_name_prefix` namespaces that group's model ids (e.g. `u:<prefix>/complex` for the LiteLLM virtual group; empty for native passthrough providers). `api` is the OpenClaw provider API hint and is omitted for native providers.
- `text_model` — `{ primary, secondary }`, each a `{ group, model }` ref into a declared group. Agents pick `primary` or `secondary` via their `model` field.
- `image_model`, `video_model`, `pdf_model` — optional `{ primary, fallbacks[] }` blocks of model refs.
- `compaction_model`, `memory_flush_model` — optional single model refs; fall back to `text_model.primary` when omitted.

### Agents block

`agents` describes the agent topology and supplies each agent's **already-rendered** prompt content. The agent writes this content verbatim into the bundle workspaces (`<id>/AGENTS.md`, `SOUL.md`, `IDENTITY.md`, `USER.md`, `TOOLS.md`, `HEARTBEAT.md`, and `<id>/skills/<name>/SKILL.md`); it never assembles or renders it.

- Defaults: `thinking_default`, `model_default` (`primary`/`secondary`), `browser_enabled_default`, `image_generation_default`, `video_generation_default`.
- `main_agent` — the entry-point agent. `subagents[]` — ordered subagents it can delegate to.
- Each agent: `id`, `name`, `model` (`primary`/`secondary`), optional `thinking`, `browser_enabled`, `image_generation`, `video_generation` (falling back to the defaults), and a `content` object.
- `content`: required `instructions`, optional `soul` / `identity` / `user_context` / `tools_doc` / `heartbeat`, and a `skills[]` list of `{ name, content }`. The `content` strings are the final prompt text — there is no template substitution, variable interpolation, or skill lookup performed by the agent.

The agent derives each agent's OpenClaw `tools.allow` / `tools.deny` from its flags and entry-point status.

### Channels and toggles

- `channels.primary` — `sellerclaw-ui` or `telegram`.
- `channels.telegram` — `{ enabled, bot_token, allowed_user_ids[], allowed_group_ids[] }`. Required and must be enabled when `primary` is `telegram`.

Deployment-specific values are **not** part of the manifest:

- The SellerClaw API host is read by the agent from `SELLERCLAW_API_URL` (used both as the OpenClaw plugin `apiBaseUrl` for `sellerclaw-ui` and to derive `SELLERCLAW_AGENT_API_BASE_URL` together with the manifest-supplied `agent_api_base_path`).
- Allowed CORS origins for the OpenClaw gateway UI come from `SELLERCLAW_WEB_URL` and `ADMIN_URL`.

### Bundle archive and secrets

`GET /bundle/archive` returns `openclaw/openclaw.json` built from the saved manifest. When web search is enabled, that JSON includes `plugins.entries["sellerclaw-web-search"].config.webSearch.authToken` — the same **outgoing** cloud agent bearer (`sca_…`) as in `agent_token.json` / `AGENT_API_KEY`. Treat the archive like credential material: only fetch it over a trusted path, and rely on the **local** control-plane bearer (`SELLERCLAW_LOCAL_API_KEY`) to protect the endpoint.

## Versioning

- **Additive changes** — adding new optional top-level fields or properties (with `additionalProperties: true` at the root) is backward compatible. Agents that don't know a new field ignore it.
- **Breaking changes** — renaming or removing fields, narrowing an enum, or changing the type of an existing field is breaking. It requires a new major version of the contract with a fresh `$id`, and coordinated updates on every client.

The `web_search` object was narrowed to an `enabled` flag only (no `provider` / `api_key` in the wire format). During monolith rollout, `POST /manifest` may still receive legacy `web_search` keys; they are **ignored** by the agent and are **not** written back to disk. The JSON Schema allows extra properties under `web_search` so validators do not reject transitional payloads.

The current schema is **v2** (`agent-manifest-v2.json`), which replaced an earlier flat manifest. Agent topology, capabilities, LLM routing, and pre-rendered prompt content are all carried explicitly in the blocks described above; clients must send the v2 shape.

One detail to preserve end-to-end: each provider group's `model_name_prefix` must be applied to that group's model ids so the OpenClaw config namespaces them correctly (e.g. `litellm/u:<prefix>/complex`).

## Control plane vs OpenClaw gateway

The agent's HTTP API (FastAPI) — including `/manifest` and `/bundle/archive` — lives on the **control plane** port (default `8001`). The OpenClaw gateway UI runs on a **different** port (default `7788`) inside the same container, managed by `supervisord`.

Orchestrators should always talk to the control-plane URL, never to the gateway directly. The control plane is what understands the manifest; the gateway only consumes the rendered bundle from disk.

## See also

- [Documentation index](../README.md)
- [CLI reference](../cli.md)
- [Agent manifest JSON Schema](./agent-manifest-schema.json)
- [Example manifest](./agent-manifest.example.json)
