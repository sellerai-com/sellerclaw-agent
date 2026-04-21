.PHONY: install setup up up-stage up-dev down test test_unit test_unit_dirs test_cloud lint \
	openclaw-skills openclaw-plugins openclaw-measure-gateway-memory openclaw-measure-gateway-memory-cold

UV ?= uv
LINT_PATHS = sellerclaw_agent tests

DOCKER_COMPOSE ?= docker compose
OPENCLAW_MEASURE_INTERVAL ?= 1
OPENCLAW_MEASURE_SAMPLES ?= 120
# Optional second env file (gitignored); omit if you rely on auto-generated local API key only.
SECRETS_ENV_FILE := $(wildcard secrets.env)
COMPOSE_SECRETS := $(if $(SECRETS_ENV_FILE),--env-file secrets.env,)

install:
	$(UV) sync --extra server --extra cli

setup:
	$(UV) run sellerclaw-agent setup

up:
	$(DOCKER_COMPOSE) --env-file .env.production $(COMPOSE_SECRETS) up server --build

up-stage:
	$(DOCKER_COMPOSE) --env-file .env.staging $(COMPOSE_SECRETS) up server --build

up-dev:
	$(DOCKER_COMPOSE) --env-file .env.local $(COMPOSE_SECRETS) up --build

down:
	$(DOCKER_COMPOSE) down --remove-orphans

test:
	$(UV) run python -m pytest tests

test_unit:
	$(UV) run python -m pytest tests -m unit

test_unit_dirs:
	$(UV) run python -m pytest tests/unit

test_cloud:
	$(UV) run python -m pytest tests/cloud

lint:
	$(UV) run ruff check $(LINT_PATHS)
	$(UV) run pyright

openclaw-skills:
	$(DOCKER_COMPOSE) exec server bash -lc 'node openclaw.mjs skills list'

openclaw-plugins:
	$(DOCKER_COMPOSE) exec server bash -lc 'node openclaw.mjs plugins list'

# Node argv is "openclaw" / "openclaw-gateway" (not openclaw.mjs).
openclaw-measure-gateway-memory:
	@$(DOCKER_COMPOSE) exec server bash -lc '\
		pid=$$(pidof openclaw-gateway 2>/dev/null | awk "{print \$$1}"); \
		if [ -z "$$pid" ]; then pid=$$(pidof openclaw 2>/dev/null | awk "{print \$$1}"); fi; \
		if [ -z "$$pid" ]; then echo "openclaw gateway not found. Is server up (make up, make up-stage, or make up-dev)?" >&2; exit 1; fi; \
		echo "[measure] pid=$$pid interval=$(OPENCLAW_MEASURE_INTERVAL)s samples=$(OPENCLAW_MEASURE_SAMPLES) OPENCLAW_NODE_MAX_OLD_SPACE_SIZE_MB=$${OPENCLAW_NODE_MAX_OLD_SPACE_SIZE_MB:-2048}"; \
		python -m openclaw_diagnostics cgroup-limits || true; \
		python -m openclaw_diagnostics monitor-memory --pid "$$pid" --interval $(OPENCLAW_MEASURE_INTERVAL) --max-samples $(OPENCLAW_MEASURE_SAMPLES)'

openclaw-measure-gateway-memory-cold:
	@echo "[measure] restarting server, then sampling (interval=$(OPENCLAW_MEASURE_INTERVAL)s samples=$(OPENCLAW_MEASURE_SAMPLES))"
	@$(DOCKER_COMPOSE) restart server
	@sleep 2
	@$(DOCKER_COMPOSE) exec server bash -lc '\
		pid=""; \
		for i in $$(seq 1 180); do \
		  pid=$$(pidof openclaw-gateway 2>/dev/null | awk "{print \$$1}"); \
		  if [ -z "$$pid" ]; then pid=$$(pidof openclaw 2>/dev/null | awk "{print \$$1}"); fi; \
		  if [ -n "$$pid" ]; then break; fi; \
		  sleep 0.5; \
		done; \
		if [ -z "$$pid" ]; then echo "timeout: openclaw / openclaw-gateway did not appear" >&2; exit 1; fi; \
		echo "[measure] pid=$$pid OPENCLAW_NODE_MAX_OLD_SPACE_SIZE_MB=$${OPENCLAW_NODE_MAX_OLD_SPACE_SIZE_MB:-2048}"; \
		python -m openclaw_diagnostics cgroup-limits || true; \
		python -m openclaw_diagnostics monitor-memory --pid "$$pid" --interval $(OPENCLAW_MEASURE_INTERVAL) --max-samples $(OPENCLAW_MEASURE_SAMPLES)'
