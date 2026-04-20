# Admin UI

The admin UI is a small Vue 3 single-page application that lets developers sign in to SellerClaw and inspect or edit the agent manifest through the browser. It talks to the local FastAPI agent over HTTP; the agent in turn talks to the remote SellerClaw API on behalf of the user.

## Location

- Source: [`sellerclaw-agent/admin-ui/`](../../admin-ui/)
- App shell: [`src/views/AppLayout.vue`](../../admin-ui/src/views/AppLayout.vue) — checks auth status and renders either the sign-in form or the manifest editor.
- Sign-in form: [`src/views/ConnectView.vue`](../../admin-ui/src/views/ConnectView.vue)
- Manifest editor: [`src/views/ManifestView.vue`](../../admin-ui/src/views/ManifestView.vue)
- Shared axios client: [`src/api/client.ts`](../../admin-ui/src/api/client.ts)
- API wrappers: [`src/api/agent.ts`](../../admin-ui/src/api/agent.ts) (manifest) and [`src/api/auth.ts`](../../admin-ui/src/api/auth.ts) (auth)
- TypeScript models: [`src/types/manifest.ts`](../../admin-ui/src/types/manifest.ts), [`src/types/auth.ts`](../../admin-ui/src/types/auth.ts)

## Running locally

Everything runs in Docker — no Node or Python dependencies are required on the host.

```sh
cd sellerclaw-agent
make up
```

`make up` starts two services defined in [`docker-compose.yml`](../../docker-compose.yml):

| Service    | URL                              | Role                                                                       |
| ---------- | -------------------------------- | -------------------------------------------------------------------------- |
| `server`   | `http://localhost:8001`          | FastAPI agent, exposes the `/manifest` and `/auth/*` routes.               |
| `admin-ui` | `http://localhost:5174/admin/`   | Vite dev server with hot module reload for the Vue SPA.                    |

The upstream SellerClaw API the agent authenticates against is configured through the `SELLERCLAW_API_URL` environment variable on the `server` service (see [`docker-compose.yml`](../../docker-compose.yml)).

Open `http://localhost:5174/admin/` in the browser. The trailing slash matters — Vite serves the app under the `/admin/` base path so that the same bundle can be mounted at `/admin` by the FastAPI server in production.

Stop both services with `make down`.

## How hot reload works

The `admin-ui` container is built from [`Dockerfile.admin-ui`](../../Dockerfile.admin-ui) and runs `vite --host 0.0.0.0 --port 5174`. The compose file bind-mounts `./admin-ui` into `/app` inside the container, so edits made on the host are picked up immediately by Vite. A named volume `admin_ui_node_modules` is mounted on top of `/app/node_modules` to prevent the empty host directory from masking the container's installed dependencies.

If you add a new npm dependency, rebuild the image so the new package is baked into the named volume:

```sh
docker compose build admin-ui
docker compose up -d admin-ui
```

## API contract used by the UI

The UI calls the agent over CORS using the base URL from `VITE_AGENT_BASE_URL` (see [`admin-ui/.env.development`](../../admin-ui/.env.development), defaults to `http://localhost:8001`). All requests share a single axios instance defined in [`src/api/client.ts`](../../admin-ui/src/api/client.ts).

Auth endpoints (proxied to the upstream SellerClaw API by the agent):

- `GET /auth/status` → `AuthStatusResponse` — always 200; `connected: false` when no credentials are stored.
- `POST /auth/connect` (body: `{ email, password }`) → `AuthStatusResponse` on success. Returns `401` for `CloudAuthError` (invalid credentials) and `502` for `CloudConnectionError` (upstream unreachable).
- `POST /auth/disconnect` → `{ status: "ok" }`. Clears the cached tokens on disk regardless of whether they were valid.

Manifest endpoints (only reachable after the user is signed in through the UI, though the backend does not currently gate them):

- `GET /manifest` → `{ manifest, version }` on 200, `404` with `{ detail: { code: "manifest_not_found" } }` when no manifest has been saved yet.
- `POST /manifest` → accepts a `SaveManifestRequest` payload and returns `{ status, manifest_path, version }`. Validation errors surface as `422` (Pydantic) or `400` (bundle-level validation, e.g. unknown integration kind).

The server adds a `CORSMiddleware` whose allowed origins come from the `AGENT_CORS_ORIGINS` environment variable (comma-separated). In docker-compose it is preset to `http://localhost:5174`.

Relevant backend files:

- [`sellerclaw_agent/server/app.py`](../../sellerclaw_agent/server/app.py) — routes, CORS, optional static mount for the production bundle.
- [`sellerclaw_agent/server/schemas.py`](../../sellerclaw_agent/server/schemas.py) — `SaveManifestRequest`, `GetManifestResponse`, `ConnectRequest`, `AuthStatusResponse`, `DisconnectResponse`.
- [`sellerclaw_agent/server/storage.py`](../../sellerclaw_agent/server/storage.py) — `ManifestStorage.save` / `load_with_version`, version hashing.
- [`sellerclaw_agent/cloud/`](../../sellerclaw_agent/cloud/) — upstream HTTP client, credential storage, and the `CloudAuthService` that wires them together.

## UI structure

[`AppLayout.vue`](../../admin-ui/src/views/AppLayout.vue) is the root view. On mount it calls `getAuthStatus()`:

- When the agent replies with `connected: false`, the layout renders [`ConnectView.vue`](../../admin-ui/src/views/ConnectView.vue) — a minimal email/password form that calls `connectSellerClaw()` and emits a `connected` event on success. Backend errors (`401` / `502`) are surfaced inline.
- When the agent replies with `connected: true`, the header shows the signed-in user and a `Sign out` button (`disconnectSellerClaw()`), and the layout renders [`ManifestView.vue`](../../admin-ui/src/views/ManifestView.vue).

`ManifestView.vue` has two sections:

1. **Current manifest** — on mount it calls `getManifest()`, shows the `version` badge and a pretty-printed JSON view. A `Refresh` button reloads the state. Empty state is rendered when the agent returns 404.
2. **Save manifest** — a full-width `<textarea>` prefilled with the current manifest (or the default template from [`src/manifestTemplate.ts`](../../admin-ui/src/manifestTemplate.ts) when none exists). `Save` parses the JSON client-side and posts it; the new version is displayed on success, and backend errors are rendered inline. `Reset` rolls the editor back to the currently saved manifest.

The MVP intentionally uses a raw JSON editor rather than a structured form. A richer form-based manifest builder can be layered on top later without touching the API.

## Production bundle

The production image is the combined OpenClaw runtime + agent (`runtime/Dockerfile`, target `staging`): a `node:20-alpine` stage runs `npm run build` for `admin-ui/`, and the resulting `dist/` is copied into the image at `/app/admin-ui/dist`. The agent reads `AGENT_ADMIN_UI_DIST` (defaulting to that path) and, when the directory exists, mounts it as `StaticFiles` on `/admin`. The SPA is therefore served same-origin from `http://<host>:8001/admin/` and no CORS is required.

During development the built bundle is absent from the image unless you explicitly `docker compose build server`, so the `/admin` mount on the server is disabled and the dev container on 5174 is the only way to access the UI.

## Tests

Backend coverage for the endpoints consumed by the UI:

- Manifest routes — [`tests/unit/server/test_app.py`](../../tests/unit/server/test_app.py): happy path, validation errors, `GET /manifest` empty/populated/version-stable.
- Auth routes — [`tests/cloud/test_app_auth.py`](../../tests/cloud/test_app_auth.py): connect/status/disconnect round-trip, `401` on `CloudAuthError`, `502` on `CloudConnectionError`.
- Supporting cloud unit tests — [`tests/cloud/test_auth_client.py`](../../tests/cloud/test_auth_client.py), [`tests/cloud/test_credentials.py`](../../tests/cloud/test_credentials.py), [`tests/cloud/test_service.py`](../../tests/cloud/test_service.py).

Run them from the repository root with `make test_unit`. There are no frontend tests in the MVP.
