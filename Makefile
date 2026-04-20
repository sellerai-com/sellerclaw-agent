.PHONY: setup up up-server up-server-eval down test test_unit lint \
	openclaw-skills openclaw-plugins openclaw-measure-gateway-memory openclaw-measure-gateway-memory-cold \
	deploy-ghcr

DOCKER_COMPOSE ?= docker compose
DOCKER_COMPOSE_EVAL = $(DOCKER_COMPOSE) -f docker-compose.yml -f docker-compose.eval.yml
# Match root Makefile eval / dev defaults (gateway published on host)
OPENCLAW_MEASURE_INTERVAL ?= 1
OPENCLAW_MEASURE_SAMPLES ?= 120

# Build/push agent runtime to GHCR (same Dockerfile/target as .github/workflows/build-agent-runtime.yml).
# Context is the monorepo root; override GHCR_OWNER or GHCR_IMAGE for your fork.
# Pushes linux/amd64 only (matches Fly / CI). Default tag is manual-<git-user>-<short-sha>[-dirty] so CI
# tags (main, latest, sha-*, v<digit>…, agent-v…) are never overwritten. Does not run CI-only manifest-schema checks.
SELLERCLAW_AGENT_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
REPO_ROOT := $(abspath $(SELLERCLAW_AGENT_DIR)/..)
RUNTIME_DOCKERFILE := $(SELLERCLAW_AGENT_DIR)/runtime/Dockerfile
BUILDX_BUILDER ?= sellerclaw-agent-builder
GHCR_OWNER ?= sellerai-com
GHCR_IMAGE ?= ghcr.io/$(shell echo $(GHCR_OWNER) | tr '[:upper:]' '[:lower:]')/sellerclaw-agent
IMAGE_TAG ?= $(shell \
	repo='$(REPO_ROOT)'; \
	short=$$(git -C "$$repo" rev-parse --short HEAD 2>/dev/null || echo unknown); \
	u=$$(git -C "$$repo" config user.name 2>/dev/null | tr '[:upper:]' '[:lower:]' | tr ' ' '-' | tr -cd 'a-z0-9._-' | cut -c1-40); \
	[ -z "$$u" ] && u=$$(whoami | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9._-'); \
	dirty=""; [ -n "$$(git -C "$$repo" status --porcelain 2>/dev/null)" ] && dirty="-dirty"; \
	printf 'manual-%s-%s%s' "$$u" "$$short" "$$dirty")
GHCR_TOKEN ?= $(GITHUB_TOKEN)
export GHCR_TOKEN GHCR_USERNAME

setup:
	uv run sellerclaw-agent setup

up:
	$(DOCKER_COMPOSE) up --build

up-server:
	$(DOCKER_COMPOSE) up server --build -d

up-server-eval:
	$(DOCKER_COMPOSE_EVAL) up server --build -d

down:
	$(DOCKER_COMPOSE_EVAL) down --remove-orphans

test:
	uv run --with-requirements requirements.dev.txt pytest tests

test_unit:
	uv run --with-requirements requirements.dev.txt pytest tests -m unit

lint:
	uv run --with-requirements requirements.dev.txt ruff check sellerclaw_agent tests
	uv run --with-requirements requirements.dev.txt pyright

# Optional: export GHCR_TOKEN (or GITHUB_TOKEN) + GHCR_USERNAME before make, or pass on the command line.
deploy-ghcr:
	@set -e; \
	if ! docker info >/dev/null 2>&1; then echo "docker daemon is not running or not reachable" >&2; exit 1; fi; \
	tag="$(IMAGE_TAG)"; \
	case "$$tag" in \
		main|latest) echo "refusing IMAGE_TAG=$$tag (reserved for CI); use a manual tag or omit IMAGE_TAG for the default" >&2; exit 1 ;; \
		sha-*|agent-v*|v[0-9]*) echo "refusing IMAGE_TAG=$$tag (reserved for CI); use a manual tag or omit IMAGE_TAG for the default" >&2; exit 1 ;; \
	esac; \
	docker buildx create --name "$(BUILDX_BUILDER)" --driver docker-container --use 2>/dev/null \
		|| docker buildx use "$(BUILDX_BUILDER)"; \
	if [ -n "$$GHCR_TOKEN" ]; then \
		if [ -z "$$GHCR_USERNAME" ]; then echo "GHCR_USERNAME is required when GHCR_TOKEN is set (GitHub username for ghcr.io)" >&2; exit 1; fi; \
		printf '%s\n' "$$GHCR_TOKEN" | docker login ghcr.io -u "$$GHCR_USERNAME" --password-stdin; \
	fi; \
	docker buildx build --platform linux/amd64 --push \
		-f "$(RUNTIME_DOCKERFILE)" --target staging \
		-t "$(GHCR_IMAGE):$(IMAGE_TAG)" \
		"$(REPO_ROOT)"; \
	echo "SELLERCLAW_AGENT_IMAGE=$(GHCR_IMAGE):$(IMAGE_TAG)"

openclaw-skills:
	$(DOCKER_COMPOSE) exec server bash -lc 'node openclaw.mjs skills list'

openclaw-plugins:
	$(DOCKER_COMPOSE) exec server bash -lc 'node openclaw.mjs plugins list'

# Node argv is "openclaw" / "openclaw-gateway" (not openclaw.mjs).
openclaw-measure-gateway-memory:
	@$(DOCKER_COMPOSE) exec server bash -lc '\
		pid=$$(pidof openclaw-gateway 2>/dev/null | awk "{print \$$1}"); \
		if [ -z "$$pid" ]; then pid=$$(pidof openclaw 2>/dev/null | awk "{print \$$1}"); fi; \
		if [ -z "$$pid" ]; then echo "openclaw gateway not found. Is server up (make up or make up-server)?" >&2; exit 1; fi; \
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
