# Local gateway secrets — migration plan

## Решение

`gateway_token` и `hooks_token` уезжают из контракта манифеста и генерируются локально, как уже сделано для `local_api_key`. Все локальные секреты консолидируются в **один файл** `<SELLERCLAW_DATA_DIR>/secrets.json` (mode 0600):

```json
{ "local_api_key": "...", "gateway_token": "...", "hooks_token": "..." }
```

- Генерация: `secrets.token_urlsafe(32)` без префиксов (симметрично с текущим `local_api_key`).
- ENV-override per-key: `SELLERCLAW_LOCAL_API_KEY` (существующий), `SELLERCLAW_GATEWAY_TOKEN`, `SELLERCLAW_HOOKS_TOKEN`.
- Миграция: при первом чтении, если `secrets.json` нет, а легаси-файл `local_api_key` есть — забрать значение оттуда, записать в `secrets.json`, удалить легаси-файл.
- Контракт манифеста: поля становятся optional в JSON-схеме, на `POST /manifest` игнорируются, с одним `WARN` за процесс на каждое устаревшее поле.

## Todos

- [ ] **red-secrets-store** — `tests/unit/server/test_secrets_store.py`: load-or-create, ENV-override per ключ, идемпотентность, mode 0600, миграция из легаси `local_api_key` (старый файл удаляется после успешной записи `secrets.json`), отсутствие легаси-файла → чистая генерация.
- [ ] **red-bundle-decoupling** — обновить `tests/unit/bundle/test_config_generator.py`, `test_builder.py`, `test_manifest.py`: `gateway_token`/`hooks_token` подаются в `render_openclaw_config` / `BundleBuilder` отдельным параметром, в `BundleManifest` их нет; рендер `openclaw.json` всё ещё содержит токены в `gateway.auth.token`, `hooks.token`, `sellerclaw-ui.config.internalWebhookSecret`.
- [ ] **red-consumers** — `tests/unit/server/test_media_upload.py`, `tests/cloud/test_chat_listener.py`: токен берётся из secrets store, не из манифеста.
- [ ] **red-manifest-contract** — `tests/unit/contracts/test_agent_manifest_schema.py`, `tests/unit/server/test_app.py`: легаси-манифест с `gateway_token`/`hooks_token` принимается, поля молча игнорируются и не возвращаются `GET /manifest`; один `WARN` за процесс на устаревшее поле.
- [ ] **developer-approval-tests** — открытый вопрос: подтвердить состав RED-тестов и публичный API нового модуля (`get_local_api_key`, `get_gateway_token`, `get_hooks_token` или единый `get_secrets()`?). **Stop-gate.**
- [ ] **green-secrets-store** — реализовать `sellerclaw_agent/server/secrets_store.py`. Удалить `local_api_key.py` (или оставить как тонкий шим на новый модуль — решить на approval'е).
- [ ] **green-bundle-decoupling** — снять поля с `BundleManifest`; `render_openclaw_config(...)` / `BundleBuilder` принимают токены параметрами; `server/app.py` `/bundle/archive` тянет их из secrets store.
- [ ] **green-consumers** — `chat_listener.py`, `server/media_upload.py` читают из secrets store.
- [ ] **green-contract-soften** — `bundle/manifest.py` (drop fields из dataclass и `to_save_manifest_mapping`), `server/schemas.py` (поля Optional, deprecated, игнорируются), `docs/contracts/agent-manifest-schema.json` (снять с `required`, описание с `"deprecated"`), `docs/contracts/agent-manifest.md`, `agent-manifest.example.json` обновить.
- [ ] **green-admin-ui** — убрать поля из `admin-ui/src/manifestTemplate.ts`, `types/manifest.ts`, `views/ManifestView.vue`.
- [ ] **green-fixtures-sweep** — `tests/unit/server/conftest.py`, `sellerclaw_agent/test_manifest_fixtures.py` и прочие фикстуры: убрать поля.
- [ ] **validate** — `make lint`, `make test_unit`. Если интеграционные тесты трогают bundle/manifest — `make test_integration` (требует `make up-dev`).
