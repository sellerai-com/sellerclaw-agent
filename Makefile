.PHONY: install setup up up-stage up-dev down test test_unit test_unit_dirs test_cloud lint \
	openclaw-skills openclaw-plugins openclaw-measure-gateway-memory openclaw-measure-gateway-memory-cold \
	release

UV ?= uv
LINT_PATHS = sellerclaw_agent tests

DOCKER_COMPOSE ?= docker compose

# Agent version reported to sellerclaw on connect/ping. Single source of
# truth: git tags (e.g. v0.7.0). Stripped of the leading "v"; falls back to
# "0.0.0+unknown" outside a git checkout (e.g. tarball install).
SELLERCLAW_AGENT_VERSION ?= $(shell git describe --tags --dirty --always --match 'v*' 2>/dev/null | sed -E 's/^v//')
ifeq ($(strip $(SELLERCLAW_AGENT_VERSION)),)
SELLERCLAW_AGENT_VERSION := 0.0.0+unknown
endif
export SELLERCLAW_AGENT_VERSION
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

# Release: create and push a new git tag (default: bump minor, e.g. v0.7.0 -> v0.8.0).
# Usage:
#   make release                # bump minor:  v0.7.0 -> v0.8.0
#   make release PART=patch     # bump patch:  v0.7.0 -> v0.7.1
#   make release PART=major     # bump major:  v0.7.0 -> v1.0.0
#   make release VERSION=1.2.3  # explicit version (without leading v)
# Add ALLOW_DIRTY=1 to skip the clean-working-tree check.
PART ?= minor
REMOTE ?= origin

release:
	@set -eu; \
	if [ -z "$${ALLOW_DIRTY:-}" ] && [ -n "$$(git status --porcelain)" ]; then \
	  echo "Working tree is dirty. Commit/stash changes or rerun with ALLOW_DIRTY=1." >&2; \
	  exit 1; \
	fi; \
	git fetch --tags --quiet $(REMOTE); \
	if [ -n "$${VERSION:-}" ]; then \
	  new="$$VERSION"; \
	else \
	  last=$$(git tag --list 'v*' --sort=-v:refname | head -n1); \
	  if [ -z "$$last" ]; then last="v0.0.0"; fi; \
	  base=$${last#v}; \
	  major=$$(echo "$$base" | cut -d. -f1); \
	  minor=$$(echo "$$base" | cut -d. -f2); \
	  patch=$$(echo "$$base" | cut -d. -f3); \
	  case "$(PART)" in \
	    major) new="$$((major+1)).0.0" ;; \
	    minor) new="$$major.$$((minor+1)).0" ;; \
	    patch) new="$$major.$$minor.$$((patch+1))" ;; \
	    *) echo "Unknown PART=$(PART) (use major|minor|patch)" >&2; exit 1 ;; \
	  esac; \
	fi; \
	tag="v$$new"; \
	if git rev-parse -q --verify "refs/tags/$$tag" >/dev/null; then \
	  echo "Tag $$tag already exists locally." >&2; exit 1; \
	fi; \
	if git ls-remote --exit-code --tags $(REMOTE) "refs/tags/$$tag" >/dev/null 2>&1; then \
	  echo "Tag $$tag already exists on $(REMOTE)." >&2; exit 1; \
	fi; \
	echo "Creating annotated tag $$tag and pushing to $(REMOTE)..."; \
	git tag -a "$$tag" -m "Release $$tag"; \
	git push $(REMOTE) "$$tag"; \
	echo "Pushed $$tag. The 'Build Image' workflow will publish the GHCR image and GitHub release."

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
