# `setup.sh` live status watch — plan

## Решение

После успешного коннекта `cmd_setup` входит в цикл наблюдения через Rich `Live`-блок. `Ctrl+C` выходит из watch'а, контейнер продолжает работать. `./setup.sh status` остаётся one-shot (без скоупа).

Что показывается в блоке (источник — существующий `_get_health_snapshot` → `/health`):

- **Cloud session**: `connected` (зелёный) / `disconnected` (красный) — из `session.connected`.
- **Chat SSE**: `connected` / `reconnecting` — из `tasks.chat_sse.connected`.
- **Last cloud ping**: `Ns ago`, цвета: зелёный <30c, жёлтый <120c, красный ≥120c или `null`. Источник — `tasks.ping_loop.last_success_at`.
- **Last error** (если есть): первая непустая строка из `tasks.*.last_error`, обрезка ≤200 символов.

Поведение:
- Опрос каждые 2с, ошибки `httpx`/JSON ловятся и отображаются в строке `Last error`, цикл не падает.
- `KeyboardInterrupt` → выход из `Live`, печать одной строки `[hint]Watch stopped. Container keeps running. ./setup.sh status — снимок, ./setup.sh stop — остановить.[/hint]`, `return 0`.
- Текущая разовая проверка `_wait_for_cloud_live` (45с до первого `session_ok && ping_ok`) **остаётся** как gate на старте: если в её рамках облако так и не подтвердило связь — печатаем фейл и не входим в watch.
- В watch заходим только при `connected == true` (как сейчас). Если юзер пропустил коннект — печатаем `_print_ready(connected=False)` и выходим, как сегодня.

## Todos

- [ ] **red-watch-renderer** — `tests/unit/cli/test_status_watch.py`: чистая функция `render_status_panel(snapshot: dict, *, now: float) -> RenderableType`, проверяем форматирование/цвета на parametrized snapshots (connected+fresh, connected+stale-ping, disconnected, missing-fields, with-error).
- [ ] **red-watch-loop** — тот же файл: `run_status_watch(base_url, console, *, poll_interval, get_snapshot, sleep, now)` с инжектируемыми зависимостями; покрытие — несколько тиков, обработка `httpx.RequestError` в одном из тиков (показывается в Last error, цикл продолжается), `KeyboardInterrupt` → возврат 0.
- [ ] **developer-approval-watch-ui** — открытый вопрос: подтвердить набор полей и пороги цветов (30c / 120c). **Stop-gate.**
- [ ] **green-watch** — реализовать `render_status_panel` и `run_status_watch` в `sellerclaw_agent/cli.py` (или вынести в `sellerclaw_agent/cli_watch.py`, если файл `cli.py` уже >1000 строк — он 1082, так что вынос оправдан).
- [ ] **green-cmd-setup** — после `_print_ready(connected=True)` вместо `return 0` вызывать `run_status_watch(base, console)`. `_print_ready` оставить — он печатает финальный panel перед watch'ем.
- [ ] **validate** — `make lint`, `make test_unit`. Ручная проверка: `./setup.sh` после успешного коннекта показывает live-блок; `Ctrl+C` корректно выходит, `docker compose ps` показывает что контейнер жив.
