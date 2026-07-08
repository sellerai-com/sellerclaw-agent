.PHONY: install setup up up-stage up-dev down test test_unit test_unit_dirs test_cloud lint \
	openclaw-skills openclaw-plugins openclaw-measure-gateway-memory openclaw-measure-gateway-memory-cold \
	release stage-cli-wheel dev-cli-local dev-cli-pypi

UV ?= uv
LINT_PATHS = sellerclaw_agent tests

DOCKER_COMPOSE ?= docker compose

# --- Developer-only local sellerclaw-cli override (never used by ./setup.sh) ---
# Each developer points SELLERCLAW_CLI_LOCAL_PATH at their own sellerclaw-cli
# source checkout in a gitignored dev.env (copy dev.env.example). When set, the
# Makefile builds a wheel into runtime/.local-wheels/ and tells the image build to
# install sellerclaw-cli from it instead of PyPI. Unset => pypi (production parity).
-include dev.env
export SELLERCLAW_CLI_LOCAL_PATH
SELLERCLAW_CLI_SOURCE := $(if $(strip $(SELLERCLAW_CLI_LOCAL_PATH)),local,pypi)
export SELLERCLAW_CLI_SOURCE
LOCAL_WHEEL_DIR := runtime/.local-wheels

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
	$(UV) sync --extra server
ifeq ($(SELLERCLAW_CLI_SOURCE),local)
	@echo "==> dev.env: linking host venv's sellerclaw-cli (editable) from $(SELLERCLAW_CLI_LOCAL_PATH)"
	$(UV) pip install -e "$(SELLERCLAW_CLI_LOCAL_PATH)"
endif

setup:
	$(UV) run sellerclaw-agent setup

# Stage the local sellerclaw-cli wheel for the image build (no-op unless dev.env
# sets SELLERCLAW_CLI_LOCAL_PATH). Also clears any stale wheel in pypi mode so a
# leftover dev wheel can never leak into a production-parity build.
stage-cli-wheel:
ifeq ($(SELLERCLAW_CLI_SOURCE),local)
	@test -d "$(SELLERCLAW_CLI_LOCAL_PATH)" || { echo "SELLERCLAW_CLI_LOCAL_PATH '$(SELLERCLAW_CLI_LOCAL_PATH)' is not a directory (check dev.env)"; exit 1; }
	@echo "==> Building sellerclaw-cli wheel from $(SELLERCLAW_CLI_LOCAL_PATH)"
	@mkdir -p $(LOCAL_WHEEL_DIR)
	@rm -f $(LOCAL_WHEEL_DIR)/*.whl
	$(UV) build --wheel "$(SELLERCLAW_CLI_LOCAL_PATH)" -o $(LOCAL_WHEEL_DIR)
	@# uv drops a `.gitignore` (`*`) in the out-dir; remove it so the committed
	@# .gitkeep stays tracked (ignoring is handled by the repo-root .gitignore).
	@rm -f $(LOCAL_WHEEL_DIR)/.gitignore
else
	@rm -f $(LOCAL_WHEEL_DIR)/*.whl 2>/dev/null || true
endif

# Re-link / revert the host venv's sellerclaw-cli. `uv run`/`uv sync` re-resolve
# from the lock and revert the editable link, so re-run dev-cli-local afterwards
# (or invoke the host CLI with `uv run --no-sync sellerclaw …`).
dev-cli-local:
	@test -n "$(strip $(SELLERCLAW_CLI_LOCAL_PATH))" || { echo "Set SELLERCLAW_CLI_LOCAL_PATH in dev.env first"; exit 1; }
	$(UV) pip install -e "$(SELLERCLAW_CLI_LOCAL_PATH)"

dev-cli-pypi:
	$(UV) sync --extra server

up: stage-cli-wheel
	$(DOCKER_COMPOSE) --env-file .env.production $(COMPOSE_SECRETS) up server --build

up-stage: stage-cli-wheel
	$(DOCKER_COMPOSE) --env-file .env.staging $(COMPOSE_SECRETS) up server --build

up-dev: stage-cli-wheel
	$(DOCKER_COMPOSE) --env-file .env.local $(COMPOSE_SECRETS) up --build

down:
	$(DOCKER_COMPOSE) down --remove-orphans

test:
	$(UV) run --extra server python -m pytest tests

test_unit:
	$(UV) run --extra server python -m pytest tests -m unit

test_unit_dirs:
	$(UV) run --extra server python -m pytest tests/unit

test_cloud:
	$(UV) run --extra server python -m pytest tests/cloud

lint:
	$(UV) run --extra server ruff check $(LINT_PATHS)
	$(UV) run --extra server pyright

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
