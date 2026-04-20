# Backend Development Guidelines

## 1) Process and Security

- **Act autonomously** and aim for a green result.
- **Iterative checks**: first narrow (specific test/file), then broader.

### Verification workflow (after any relevant code change)

1. `make lint` — ruff + pyright. Fix all new errors before proceeding.
2. `make test_unit` — run the full unit test suite.
3. If infra/ORM/views changed → `make test_integration` (requires running stack, see Commands).
4. **Never** consider a task done until all applicable steps pass.

### Safe changes (mandatory checklist)

- Changed signature/contract → update **all** usage locations (including fakes/fixtures).
- Renamed/deleted → update **all** references.
- Changed ORM fields → update **mappers / schemas / views / services**.
- Do not break existing tests without a clear reason.
- Do not duplicate fixtures/fakes: find existing ones first.

### Local logs

- Readable local console logs: `docker compose logs ...`
- Detailed structured service logs: `logs/services/*.jsonl`
- Aggregated local error log collected by Vector: `logs/errors.jsonl`

## 2) Feature Planning and TDD

If the task is a new feature or a major refactoring, first a plan (plan mode), then code.

### Plan Execution (mandatory rules)

If working according to a plan (`*.plan.md` files), follow the rules below.

- **One todo at a time**: perform tasks strictly sequentially. Do not start the next todo until the current one is completed.
- Do not create or run database migrations.
- **Approval gates — stop signal**:
 - Any todo of the form `developer-approval-*` means: **stop** and wait for explicit approval text from the developer.
 - Format of the approval question: **open-ended question without options** (the developer writes comments/additions in free text).
 - Prohibited before approval: writing implementation (GREEN), changing APIs/contracts, fixing tests "at random".
- **DEV ONLY tasks**:
 - Todo `dev-create-and-run-migrations` (and any DEV ONLY) is performed by the developer. The agent **does not generate or apply** DB migrations automatically.
 - After a DEV ONLY step, the agent continues only after the developer has confirmed the result (e.g., migrations created/applied/errors exist).
- **TDD-gate order is mandatory**:
 - RED (unit → integration) → approval → GREEN → REFACTOR/VALIDATE.
 - If tests/contracts are not approved — do not start implementation.
- **Plan modification**:
 - If it turns out during the process that the plan is incorrect/incomplete — first update the plan and pass the `developer-approval-*` gate again (if it exists), then continue.
- **Todo statuses**:
 - `in_progress` — only for the current task.
 - `completed` — set only after actual completion of the item.
 - Do not bulk close todos "retroactively".

### What to clarify before the plan

- Scope (what's included/what's not), MVP vs full.
- Errors/edge cases/validations.
- Dependencies (other modules, external systems), constraints (time/resources/tech).

### Data models first

If data models (domain/ORM) change — first fix the model:
fields, relationships, invariants, statuses/enums, migration consequences.

**DB Migrations: DEV ONLY** — the agent does not generate or apply migrations automatically.

## 3) Architecture (where to put code)

### Module structure

Each module follows the same two-layer layout:

```
src/<module>/
├── domain/
│   ├── models.py       — domain dataclasses (frozen=True)
│   ├── ports.py        — repository/service Protocol interfaces
│   ├── services.py     — business logic (no framework imports)
│   └── exceptions.py   — domain-specific exceptions (optional)
└── infra/
    ├── orm.py          — SQLAlchemy ORM models
    ├── mappers.py      — ORM ↔ domain model conversion
    ├── repos.py        — repository implementations
    ├── views.py        — FastAPI router endpoints
    ├── schemas.py      — Pydantic request/response schemas
    └── dependencies.py — FastAPI Depends() wiring
```

Cross-module utilities: `src/domain/utils/<name>.py`.

### Layers

- `domain/` — business logic. **No** framework/ORM/HTTP imports. Only stdlib + domain models/ports.
- `infra/` — infrastructure. ORM, views, repos, mappers, external integrations. Depends on domain, never the other way around.

### View / API layer pattern

- Each module exposes `router = APIRouter(prefix="/<resource>", tags=[...])` in `infra/views.py`.
- Dependencies (services, repos) are wired in `infra/dependencies.py` and injected via `Depends()`.
- Views are thin: extract params → call service → convert result to response schema.
- Authentication: `user_id: UUID = Depends(get_authenticated_user_id)`.
- Never put business logic in views — delegate to the domain service.

### Error handling

#### Domain layer

- Business rule violations → raise **domain-specific exceptions** (in the module's `exceptions.py`).
- "Not found" in commands (update/delete) → raise exception (the caller expects the entity to exist).
- "Not found" in queries (get_by_id) → return `None` (the caller decides what to do).
- Never raise generic `Exception`. Use `ValueError` only for simple input validation.

#### View / API layer

- Catch domain exceptions → convert to `HTTPException` with structured `detail={"code": ..., "message": ...}`.
- Never let domain exceptions leak to the client as 500.

### Unit of Work (UoW)

- Services accept `uow_factory: Callable[[], IUnitOfWork]`.
- Repositories are taken from `uow.repos.*`.
- Atomicity/transactions — responsibility of UoW/repository.
- Domain events/signals — **after `uow.commit()`**.

### Repos / contracts / fakes

- Repository interfaces are `Protocol` classes in `domain/ports.py`.
- Fake implementations must match the contract.
- Extended the interface → update all fakes.

### Code conventions

- **Every** `.py` file starts with `from __future__ import annotations`.
- Domain models: `@dataclass(frozen=True)` — immutable. Use `dataclasses.replace()` for updates.
- Services: `@dataclass` (mutable, holds repo/dependency references).
- Ports (repository interfaces): `Protocol` in `domain/ports.py`, not ABC.
- Type hints: mandatory on all function signatures (params + return). Use `X | None`, not `Optional[X]`.
- Naming:
  - Services: `<Entity>Service` (e.g. `OrderService`).
  - Repositories: `<Entity>Repository` (protocol), `Sql<Entity>Repository` (ORM impl), `Fake<Entity>Repository` (test fake).
  - Schemas: `<Action><Entity>Request` / `<Entity>Response` (e.g. `ConnectShopifyStoreRequest`, `SalesChannelResponse`).

## 4) Testing (essence)

Goal: fast, clear, and robust tests that check **your code**, not the correctness of third-party libraries.

### Mini-checklist (before completion)

- Unit: `pytestmark = pytest.mark.unit`.
- Async: without `@pytest.mark.asyncio` (in the project `asyncio_mode = auto`) — `async def` is sufficient.
- By default Fake > Mock; mock/patch selectively for external/expensive/non-deterministic (network, cryptography, time).
- RED/TDD: for not yet implemented, `@pytest.mark.xfail(reason="...")` is permissible — remove after implementation.
- No extra fixture parameters in the test signature (remove unused ones).
- No duplicate fixtures/fakes: find existing ones first, then add new ones.

### Structure

- Unit: `tests/unit/<module>/test_*.py`
- Integration: `tests/integration/<module>/test_*.py`
- Fixtures: `tests/unit/<module>/conftest.py`
- Fakes: `tests/unit/<module>/fakes.py`
- Samples: `tests/unit/<module>/data/samples/`

### Unit vs Integration

- **Unit** — isolated business logic (services/use cases/pure functions), without real DB/network; dependencies — via fakes/fixtures.
- **Integration** — "gluing" components and infrastructure (ORM ↔ mappers ↔ repos ↔ services ↔ views/urls if necessary).

Integration tests are appropriate when it's important to check:
- real DB queries/transactions/constraints/indexes
- correctness of ORM ↔ domain mapping
- dependency wiring/configuration at the boundary (urls/decorators/validation), if it's part of the scenario

### Do not test libraries

Do not check the correctness of third-party libraries (JWT/crypto/http clients, etc.). Check:
- your business logic around them
- your wrappers/adapters and edge cases
- that you correctly form the input data (payload/parameters)

### Fake is preferable to Mock

By default, use **Fake implementations** (in-memory repositories/adapters) and check behavior/result:
- ✅ "user created/read from repository?"
- ❌ "is `setex()` called?"

Apply mock/patch selectively when it really simplifies the test and does not worsen its robustness (e.g., time/random/network).

### Pytest fixtures (practice)

#### Fixture-factories for domain models (MANDATORY)

- **Every domain model** used in tests MUST have a corresponding `make_<model>` fixture-factory in `conftest.py`.
- Fixture-factories return `Callable[..., Model]` with sensible defaults for all fields and keyword overrides.
- Tests request only the fixture-factory and override only the fields relevant to the test scenario.
- **NEVER** create domain objects directly via constructors or plain helper functions in `test_*.py` files. Always use fixture-factories.
- Root-level factories (shared across modules) live in `tests/unit/conftest.py`. Module-specific factories live in `tests/unit/<module>/conftest.py`.
- Fixture-factories can compose: e.g. `make_order` depends on `make_shipping_address` and `make_order_line_item`.

Reference: `tests/unit/conftest.py` — `make_user`, `make_product`, `make_order`, etc.

#### Organization and reuse (no duplication)

- Fixtures → `@pytest.fixture` in `conftest.py` (root or module-level).
- Fakes → `tests/unit/<module>/fakes.py` — **never** inline in `test_*.py`.
- If 2+ test modules use the same fixture or fake → move it **up** to the nearest shared `conftest.py` or `fakes.py`.
- Before creating a new fixture/fake — **search** for an existing one in parent `conftest.py` / `fakes.py` files.
- Add **only really used** fixtures to the test signature (remove unused parameters).
- Do not check internal fields of fake objects — check the result via public methods/contracts.
- Read config/secrets for tests via the project settings file, not via `os.getenv()` directly in tests.
  - If a test critically needs a setting/secret and it's missing — the test should **fail** (fail fast), not `skip`.
  - In e2e tests, it's normal to use environment variables.

### Parametrize over data, not tests (MANDATORY)

- When the **same logic** is tested with different inputs/expected outputs → use `@pytest.mark.parametrize`. Do NOT write separate test functions for each data variant.
- Each param set MUST have a descriptive `id` via `pytest.param(..., id="...")`.
- Group at least: happy path, edge case(s), error case(s) in one parametrized test.
- If the test **setup** differs significantly between cases (not just input data), separate tests are acceptable.

Bad — separate function per input:

```python
def test_parse_valid_json():
    assert parse('{"a":1}') == {"a": 1}

def test_parse_empty_object():
    assert parse("{}") == {}
```

Good — parametrized:

```python
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param('{"a":1}', {"a": 1}, id="simple-object"),
        pytest.param("{}", {}, id="empty-object"),
        pytest.param('{"a":{"b":1}}', {"a": {"b": 1}}, id="nested"),
    ],
)
def test_parse_valid_json(raw: str, expected: dict) -> None:
    assert parse(raw) == expected
```

### Assertion depth and quality (MANDATORY)

- **NEVER** check only the count of results (`assert len(results) == 2`) without verifying their content.
- Assert on **concrete field values**, not just structure presence. Bad: `assert "name" in result`. Good: `assert result.name == "Alice"`.
- For collections: verify both the count AND the content of specific items/field values.
- For mutations (create/update/delete): verify the object's state AFTER the operation, not just that no exception was raised.
- For error cases: verify the specific exception type AND the error message/code where applicable (`pytest.raises(ValueError, match="...")`).
- When comparing complex objects, prefer direct equality (`assert result == expected_obj`) or exhaustive field-by-field assertions over partial checks.
- If a function returns a rich object, assert all semantically important fields — not just one or two.

### Edge cases and error coverage (MANDATORY)

When writing tests for any function/service, ALWAYS cover (where applicable):

1. **Happy path** — the normal successful scenario.
2. **Boundary values** — empty collections, zero, `None`, min/max values, single-element lists.
3. **Invalid input** — wrong types, missing required fields, malformed data.
4. **Duplicate/conflict** — creating something that already exists, concurrent modifications.
5. **Not found** — operating on a non-existent entity.
6. **Authorization/ownership** — accessing another user's data, expired tokens.
7. **Idempotency** — calling the same operation twice should produce predictable results.

The test file for a service/use-case should contain tests for ALL relevant categories above, not just the happy path. If a category doesn't apply — skip it consciously, not by omission.

### TDD/RED policy

- Cycle: **RED** (test fails "by design") → **GREEN** (minimal implementation) → **REFACTOR**.
- RED: tests should fail on `assert`, not on `ImportError`. If there is no implementation yet — add minimal stubs (without business logic).
- Changed/extended the port/repository interface — update all Fake implementations, otherwise tests will start failing "in the wrong place".

### Commands

**CRITICAL**: Run tests and checks ONLY via make commands below. Do NOT invoke `pytest`, `ruff`, or `pyright` directly — the Makefile sets up the correct environment, flags, and execution context.

| Command | What it does |
|---------|-------------|
| `make lint` | Static analysis (ruff + pyright) |
| `make test_unit` | Unit tests (local, no stack needed) |
| `make test_integration` | Integration tests via `docker compose exec` in the running `sellerclaw-api` container (`make up-dev` first) |
| `make up-dev` | Start the full dev stack (DB, API, agents, etc.) |
| `make marimo-up` | Start the Marimo notebook container for interactive DB work |
| `make sellerclaw-shell` | Open an IPython shell inside the notebook container |
| `make sellerclaw-exec CMD="..."` | Execute arbitrary Python code inside the notebook container |

- **Unit tests** can be run locally without the stack.
- **Integration tests** require a running dev stack (DB, Redis, MinIO, etc.). Start it first with `make up-dev`, then run `make test_integration` in a separate terminal.
- Verification order: `make lint` → `make test_unit` → `make test_integration` (if applicable).
- For ad-hoc DB work, first run `make marimo-up`, then use:
  - `make sellerclaw-shell` for an interactive IPython session in `/app/notebooks`
  - `make sellerclaw-exec CMD="from _init import *; print(sorted(models.keys())[:5])"` for one-off Python execution with preconfigured project imports
