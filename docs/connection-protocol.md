# Cloud connection protocol

When the agent is paired with a SellerClaw cloud (or any compatible control plane), it keeps the link alive over **HTTP polling**: the agent opens the session, heartbeats every ~10 seconds, and runs whatever command the server hands back. There is **no inbound traffic** to the agent — only outbound HTTPS from the user's machine.

This document describes the protocol from the **agent's perspective**: what it sends, what it expects in return, how it stores state, and how it recovers from errors. The server-side implementation is out of scope for this repository.

## Principles

- The agent initiates the connection — no public IP or open ports are required on the host.
- Each user has **at most one active edge session** at a time. Reconnecting supersedes the previous session.
- Commands arrive via **pull**: the agent fetches them as part of the next ping.
- Session state (online/offline) is ephemeral on the server side. The agent treats its own session ID as the source of truth for "am I still the active instance".

## Files on disk

The agent stores two files in `SELLERCLAW_DATA_DIR` (default `/data`):

```text
SELLERCLAW_DATA_DIR/
├── agent_token.json    — long-lived agent token (`sca_…`) and user metadata
└── edge_session.json   — agent_instance_id for the current session
```

`agent_token.json` is written atomically (temp file + rename) and holds `agent_token`, `user_id`, `user_email`, `user_name`, and `connected_at`. There is **no** user JWT and **no** refresh token on the agent. `edge_session.json` is cleared whenever the server rejects the session (HTTP 401 `agent_session_invalidated`) so the next iteration reconnects from scratch.

## Agent authentication

Sign-in (CLI password prompt or device flow) exchanges user proof for an **agent-scoped** token via cloud routes under `POST /agent/auth/*`. The agent persists that token in `agent_token.json` (or uses `AGENT_API_KEY` from the environment as an override for the same value).

The agent sends `Authorization: Bearer <agent_token>` on every cloud request. There is **no** token refresh path: on **401** the client treats auth as invalid, clears local `agent_token.json` (and related session state where applicable), and waits until the user signs in again.

**Server-side invalidation:** calling `POST /agent/connection/disconnect` (or admin suspend) revokes active agent tokens for that user in the cloud, so a copied `agent_token.json` stops working even if the file still exists locally.

## Session lifecycle

### 1. Connect

```text
Agent                                   Server
  |                                       |
  |  POST /agent/connection/connect       |
  |  { agent_version, protocol_version }  |
  |-------------------------------------->|
  |                                       |  — cancels in-flight commands of the
  |                                       |    previous session (PENDING → CANCELLED,
  |                                       |    DELIVERED → FAILED)
  |                                       |  — issues a fresh agent_instance_id
  |                                       |  — stores session state
  |  { agent_instance_id }                |
  |<--------------------------------------|
  |                                       |
  |  Writes edge_session.json             |
```

Reconnecting after a restart is automatic: the server invalidates the previous session and cleans up its unfinished commands.

### 2. Ping loop

`run_edge_ping_loop` runs in the agent's FastAPI lifespan. Every ~10 seconds it:

1. Ensures an agent token is available (`agent_token.json` or `AGENT_API_KEY`) — otherwise sleeps.
2. Probes the local OpenClaw program via `supervisorctl status` (→ `running` / `stopped` / `starting` / `error`).
3. Probes the **edge browser** stack (KasmVNC supervisord program + Chrome DevTools `GET http://127.0.0.1:<debug-port>/json/list`) and attaches an optional `browser` object to each ping when `protocol_version >= 2`.
4. Creates a session with `POST /connect` if `edge_session.json` is missing.
5. Sends `POST /agent/connection/ping` with the current status, optional `browser`, and `command_result: null`.
6. If the response contains `pending_command`, executes it (see [Commands](#commands)).
7. Sends a second ping with the `command_result` (and the latest `browser` probe) so the server can move the command to COMPLETED or FAILED.

```text
POST /agent/connection/ping
{
  "agent_instance_id": "...",
  "agent_version": "0.1.0",
  "protocol_version": 2,
  "openclaw_status": "running",
  "openclaw_error": null,
  "browser": {
    "status": "running" | "stopped" | "starting" | "error",
    "kasmvnc_running": true,
    "chrome_running": true,
    "error": null,
    "pages": [
      { "url": "https://example.com/path", "title": "Tab title", "type": "page" }
    ]
  },
  "command_result": null,
  "whatsapp": {
    "linked": true,
    "connected": true,
    "self_e164": "+15550001111",
    "logged_out": false,
    "observed_at": "2026-10-14T09:00:00+00:00"
  }
}

→ 200 { "pending_command": { "command_id", "command_type", "issued_at" } }
   or  { "pending_command": null }
```

`whatsapp` (protocol v5) is the WhatsApp channel as OpenClaw's `channels.status` last reported it. A
background task reads it every 30 s (only while the channel is configured) so a busy gateway never
delays the ping; the ping sends the last value with the time it was observed. It is **omitted**
while the channel is not configured, OpenClaw is stopped, or nothing has been observed yet —
servers must read a missing field as "not reported", never as "unlinked". `linked` is `null` while
the plugin's auth files are being written; `logged_out` means WhatsApp revoked the linked device.

`browser` is **omitted** by agents speaking `protocol_version` 1, or when the probe is skipped; servers must treat it as optional for backward compatibility. Page list is capped (≤20 URLs; URL length truncated server-side as applicable).

The server considers the agent **offline** when it has not pinged for longer than its `stale_after_seconds` threshold (default 30s on SellerClaw cloud). That status is virtual — it is computed on read, not written anywhere.

### 3. Disconnect

- **Graceful.** Agent calls `POST /agent/connection/disconnect`. The server marks the session as `disconnected` with reason `graceful`.
- **Timeout.** Agent simply stops pinging. The server virtually marks it `disconnected` with reason `timeout` on the next status read.
- **Replaced.** A fresh `connect` from another instance supersedes the current one with reason `replaced`.
- **Suspended by user.** The user can pause the agent through the cloud (`POST /user/agent/connection/disconnect`). The next `connect` / `ping` returns **403 `agent_suspended`** until the user resumes. The agent does **not** clear `agent_token.json` — it logs the state and retries on a long interval (~3 minutes).

## Commands

### Supported types

| Type | What the agent does |
|------|---------------------|
| `start` | Pulls the current manifest (`GET /edge-manifest`), stores it locally, renders the OpenClaw bundle, starts the `openclaw` program via supervisord. Rejected if the process is already running (`openclaw_already_running`). |
| `stop` | Stops and releases the OpenClaw program. Idempotent if it is already stopped. |
| `restart` | `stop` + `start`. If nothing was running, behaves like `start`. |
| `disconnect` | `stop` + `POST /disconnect` + exits the ping loop. The local FastAPI server keeps running so the user can re-authenticate. |
| `open_browser` | Starts KasmVNC + `gost` via supervisord, waits for the configured X11 socket, launches Chrome via `OPENCLAW_CHROME_LAUNCHER` (default `/usr/local/bin/openclaw_chrome`). **Rejected** while OpenClaw gateway is `running` (`openclaw_running_browser_command_rejected`). |
| `close_browser` | Best-effort `pkill` of Chrome/Chromium, then `supervisorctl stop` for KasmVNC and `gost`. Also cancels a running browser-view tunnel. Idempotent. |
| `update_manifest` | Pulls the current manifest, stores it, rebuilds the bundle and applies it in place (`openclaw_start apply-bundle`; OpenClaw hot-reloads). Skipped when the bundle on disk already matches. A proxy change is refused — it needs `restart`. |
| `whatsapp_link` | (protocol v5) Fetches the open link attempt (`GET /agent/connection/whatsapp/link` → `{login_id, force}`; 404 → **rejected**), starts the link runner in the background and acks `completed` at once. The runner waits (up to 5 min) for the WhatsApp plugin to load, calls the gateway's `web.login.start` / `web.login.wait`, and reports every step to `POST /agent/connection/whatsapp/link/{login_id}` (`preparing` → `qr` with a PNG data URL, as often as WhatsApp rotates it → `connected` with `self_e164`, or `failed` with a `reason` / `expired`). A 404 from that endpoint stops it. |
| `whatsapp_logout` | (protocol v5) Calls `channels.logout` while the plugin is loaded (stops the channel and deletes its credentials; WhatsApp itself is not told, so the phone lists the device under Linked devices until the owner removes it — without the keys it can no longer be used), deletes `~/.openclaw/credentials/whatsapp/`, and `DELETE /agent/connection/whatsapp/session`. Any gateway error other than "plugin not loaded" / "OpenClaw down" fails the command. Independently, the session backup loop deletes the local session once the applied manifest carries no WhatsApp numbers, so Disconnect holds even when this command was never delivered. |
| `browser_stream` | (protocol v3) Fetches the active browser-view session (`GET /agent/browser-view/session`), ensures the KasmVNC stack is up (standalone `open_browser` path when OpenClaw is stopped; **rejected** when OpenClaw runs without an open browser), then keeps an outbound websocket tunnel dialed to `wss://…/agent/browser-view/tunnel?session_id=…`. After the cloud sends the text frame `attach`, the agent opens `ws://127.0.0.1:6080/websockify` (Basic auth `user`/`VNC_PASSWORD`, `Origin` header, `binary` subprotocol, client pings disabled) and pipes bytes both ways. Redials while the session exists; stops on close code `4001` or denial 401/404/410. |

### WhatsApp session copy

The linked WhatsApp session lives in `~/.openclaw/credentials/whatsapp/`, which managed machines
and one-command installs lose on every recreate. The agent keeps a copy in the cloud, separate from
the browser sign-in backup so neither overwrites the other:

- `POST /agent/connection/whatsapp/session` (gzip tar) — after the files have been quiet for 60 s,
  at most every 5 min (15 min at the latest for a busy account), immediately-after-settling once a
  link completes. Every `*.json` must parse, so a file caught mid-write is never uploaded.
- `GET /agent/connection/whatsapp/session` — read by `restore_state` at boot when no local
  `creds.json` exists, also on a clean start (`RESET_STATE=1`): the link is the assistant's identity,
  not memory.
- `DELETE /agent/connection/whatsapp/session` — on logout.

These endpoints and the state backup refuse OAuth-connector and CLI tokens (403 `agent_runtime_only`).

### Command lifecycle (server view)

```text
             ┌─────────┐
             │ PENDING │  user or automation enqueued the command
             └────┬────┘
                  │
        ┌─────────┼──────────────┐
        │         │              │
        ▼         ▼              ▼
  ┌──────────┐ ┌───────────┐ ┌──────────┐
  │ DELIVERED │ │ CANCELLED │ │ CANCELLED │
  │           │ │ (user)    │ │ (expired) │
  └────┬──────┘ └───────────┘ └───────────┘
       │
  ┌────┼─────────────┐
  │    │             │
  ▼    ▼             ▼
┌──────────┐ ┌─────────┐ ┌───────────┐
│ COMPLETED │ │ FAILED  │ │ TIMED_OUT │
└──────────┘ └─────────┘ └───────────┘
```

Constraints the agent can observe in the wild:

- The server allows **at most one active command** (PENDING or DELIVERED) per user.
- Conflicting lifecycle commands (e.g. `start` while `stop` is active) are rejected at enqueue time.
- DELIVERED commands time out on the server side if the agent does not confirm within ~60s.

### Reporting results

After executing a command, the agent sends a second ping with:

```json
{
  "command_result": {
    "command_id": "...",
    "outcome": "success" | "failed" | "rejected",
    "error": "optional human-readable message"
  }
}
```

`rejected` is used when the agent refuses to run the command (e.g. `openclaw_already_running`). `failed` covers runtime errors (Docker unreachable, bundle render error, etc.).

## Error handling

### 401 `agent_session_invalidated`

The server dropped the session (replaced by another instance or expired). The agent:

1. Clears `edge_session.json`.
2. On the next iteration, calls `POST /connect` to register a fresh session.

### 403 `agent_suspended`

The user paused the agent from the cloud. Behaviour:

1. The agent does **not** clear `agent_token.json` or the session file.
2. It logs a warning and backs off to a long interval (`ping_interval_when_suspended`, ~3–3.5 minutes).
3. When the user resumes, the next `connect` succeeds.

Older agents that don't special-case this code fall into the generic error backoff (up to 5 minutes). Behaviour degrades gracefully — no tight retry loop.

### Docker / bundle errors

If the combined runtime image is unreachable, the supervisord program fails to start, or the bundle renderer raises, the agent returns `failed` with the error string. For manual `POST /openclaw/*` calls on the control plane, the same error surfaces in the HTTP response body.

### Network failures

Any transport error (timeout, 5xx, DNS) is treated as transient: the agent logs a warning and retries on the next cycle. The server's `stale_after_seconds` window is what eventually flips the user-visible status to "offline".

## Protocol version

Every ping carries `protocol_version` — the agent's own constant, not the value saved with the edge session at connect (a session saved by an older agent survives an upgrade on a persistent `/data`). The current agent version is **5**:

- **5** — understands `whatsapp_link` / `whatsapp_logout` and sends `whatsapp` in the ping (the cloud gates WhatsApp linking on `>= 5`).
- **4** — understands a hook envelope (forwards a named `/hooks/agent` hook and reports back on it).
- **3** — understands `browser_stream` and the browser-view tunnel dial-back (the cloud gates the remote browser view on `>= 3`).
- **2** — optional `browser` telemetry. **1** remains valid.

Older agents simply never receive the newer commands. Servers may refuse clients whose version they no longer support; the agent in turn can gate behaviour on the server version exposed in future responses.

## Security notes

- The agent never opens inbound ports — only outbound HTTPS.
- Credentials are stored on disk under `SELLERCLAW_DATA_DIR` with atomic writes.
- Every ping includes `agent_instance_id`; the server uses it to reject replays from superseded sessions.
- Commands are scoped to the `user_id` of the bearer token, so one user cannot drive another user's agent even with a compromised session ID.

## See also

- [Documentation index](./README.md)
- [CLI reference](./cli.md)
- [Agent manifest contract](./contracts/agent-manifest.md)
