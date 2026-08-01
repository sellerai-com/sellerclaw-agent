.PHONY: install setup up up-stage up-dev down test test_unit test_unit_dirs test_cloud lint check \
	openclaw-skills openclaw-plugins openclaw-measure-gateway-memory openclaw-measure-gateway-memory-cold \
	release release-preflight release-latest release-beta stage-cli-wheel dev-cli-local dev-cli-pypi

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

# Does the committed uv.lock still describe pyproject.toml? ci.yml and build-image.yml install with
# `uv sync --locked`, so a dependency edit pushed without its lock update fails there — this catches it
# before the push. `uv lock --check` only reports; run `uv lock` (or any `make test_unit`, which
# re-locks on the way in) to refresh, then commit uv.lock alongside the pyproject change.
lock-check:
	$(UV) lock --check

check: lock-check lint test_unit

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

# Release: create and push a new git tag. PREFER the `release-latest` / `release-beta` wrappers
# below — they compute the number for you so the pre-release format can't be wrong. `release` is the
# low-level target they delegate to; call it directly only for an explicit one-off.
# Usage:
#   make release                # bump minor:  v0.7.0 -> v0.8.0
#   make release PART=patch     # bump patch:  v0.7.0 -> v0.7.1
#   make release PART=major     # bump major:  v0.7.0 -> v1.0.0
#   make release VERSION=1.2.3  # explicit version (without leading v)
#   make release VERSION=1.2.3-beta.1  # explicit pre-release (usually use `make release-beta`)
# PART bumps the last STABLE tag — pre-releases are skipped on purpose: their number is not three
# integers, so bumping the newest tag when that is v0.60.0-beta.6 would mean `patch+1` on the string
# "0-beta". Same anchor the `release-latest` / `release-beta` wrappers use.
# Add ALLOW_DIRTY=1 to skip the clean-working-tree check.
# Every release path runs lint + unit tests first (`make check`) — see release-preflight below;
# add SKIP_CHECKS=1 to bypass that gate (emergencies only).
# Channel is decided by the version shape in build-image.yml: a SemVer pre-release segment
# (a hyphen, e.g. 1.2.3-beta.1) is a pre-release; a clean X.Y.Z from main is the real release.
PART ?= minor
REMOTE ?= origin

# Gate every release on lint + unit tests (~20s), run against the exact tree about to be tagged.
# build-image.yml does gate the image on a `verify` job (ruff + pyright + unit tests on 3.12/3.13),
# but it runs AFTER the tag exists: a failure there leaves a pushed tag with no image behind it. This
# preflight is what keeps the tag from being cut at all. It is a *prerequisite* of `release`,
# so it runs before anything is tagged or pushed and a failure leaves no tag behind. (Prerequisite,
# not an in-recipe `$(MAKE)` call: make executes recipe lines that mention $(MAKE) even under `-n`,
# which would turn a dry-run `make -n release` into a real one.)
# `lock-check` runs FIRST and is NOT skippable: ci.yml and build-image.yml install with
# `uv sync --locked`, so a stale lock rejects the tag build no matter how urgent the release is —
# skipping it only turns a 1-second failure into a burnt tag. Order matters inside `check` too:
# `uv run` re-locks on the way into the tests, so a stale lock would be silently rewritten mid-release
# and resurface as a confusing "working tree is dirty" in `release` below.
# Escape hatch: SKIP_CHECKS=1 (emergencies only — you are shipping untested code, lock excepted).
release-preflight:
	@set -e; \
	echo "release-preflight: uv.lock must match pyproject.toml (not skippable)..."; \
	$(MAKE) --no-print-directory lock-check; \
	if [ -n "$${SKIP_CHECKS:-}" ]; then \
	  echo "release-preflight: SKIP_CHECKS=1 — skipping lint + unit tests. Shipping unverified."; \
	else \
	  echo "release-preflight: lint + unit tests must pass before tagging..."; \
	  $(MAKE) --no-print-directory check; \
	  echo "release-preflight: OK."; \
	fi

release: release-preflight
	@set -eu; \
	if [ -z "$${ALLOW_DIRTY:-}" ] && [ -n "$$(git status --porcelain)" ]; then \
	  echo "Working tree is dirty. Commit/stash changes or rerun with ALLOW_DIRTY=1." >&2; \
	  exit 1; \
	fi; \
	git fetch --tags --quiet $(REMOTE); \
	if [ -n "$${VERSION:-}" ]; then \
	  new="$$VERSION"; \
	else \
	  last=$$(git tag --list 'v*' --sort=v:refname | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$$' | tail -n1); \
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

# Preferred entry points — you never type a version string, so you can't get the format wrong.
# The number is computed from existing tags:
#   make release-beta     # from dev  -> X.Y.Z-beta.N (pre-release): "Pre-release" on GitHub,
#                         #   moves the :beta image tag, never touches :latest
#   make release-latest   # from main -> X.Y.Z        (stable):      "Latest" on GitHub, moves :latest
#
# Both work toward one "base" version. Normally that is the last STABLE tag bumped by PART (minor by
# default; PART=patch|major): release-beta cuts pre-releases for it (-beta.1, -beta.2, …) and
# release-latest finalizes it to the clean X.Y.Z. Typical flow: `make release-beta` on dev (repeat) →
# `make release-latest` on main.
#
# The exception is an OPEN beta line, which both targets follow instead of bumping: betas already cut
# for a base that still outranks every stable tag. Following it is what makes PART a one-time choice
# instead of something you must retype forever — `make release-beta PART=major` opens 2.0.0-beta.1,
# and a plain `make release-beta` the next day continues that line (2.0.0-beta.2) rather than dropping
# back to 1.1.0-beta.1, a *lower* version than the beta already published.
#
# A line stays open only while its base outranks the newest stable release — not merely while its own
# vX.Y.Z tag is missing. Both endings close it: its own stable tag (v0.60.0 closes the 0.60.0-beta.N
# line) and a later release that jumped over it (v1.0.0 closes the still-unreleased 0.61.0-beta.N
# line). Once closed, the next beta opens a fresh line off the newest stable, so a superseded
# pre-release can never be cut — appending -beta.2 to a line that 1.0.0 already overtook would publish
# a build that is dead on arrival. When the repo has only beta tags and no stable one at all, the open
# line is followed as usual instead of falling back to v0.0.0.
# Delegates to `release`, which does all the tag pushing.
release-latest release-beta:
	@set -eu; \
	git fetch --tags --quiet $(REMOTE); \
	last_stable=$$(git tag --list 'v*' --sort=v:refname | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$$' | tail -n1); \
	if [ -z "$$last_stable" ]; then last_stable="v0.0.0"; fi; \
	stable=$${last_stable#v}; \
	base=""; \
	latest_beta=$$(git tag --list 'v*' --sort=-v:refname | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+-beta\.[0-9]+$$' | head -n1); \
	if [ -n "$$latest_beta" ]; then \
	  beta_base=$${latest_beta#v}; beta_base=$${beta_base%-beta.*}; \
	  if [ "$$beta_base" != "$$stable" ] && \
	     [ "$$(printf '%s\n%s\n' "$$beta_base" "$$stable" | sort -V | tail -n1)" = "$$beta_base" ]; then \
	    base="$$beta_base"; \
	    echo "$@: open beta line $$base (latest $$latest_beta, ahead of stable $$last_stable)."; \
	  else \
	    echo "$@: beta line $$beta_base is closed by stable $$last_stable — opening the next line."; \
	  fi; \
	fi; \
	if [ -z "$$base" ]; then \
	  major=$$(echo "$$stable" | cut -d. -f1); \
	  minor=$$(echo "$$stable" | cut -d. -f2); \
	  patch=$$(echo "$$stable" | cut -d. -f3); \
	  case "$(PART)" in \
	    major) base="$$((major+1)).0.0" ;; \
	    minor) base="$$major.$$((minor+1)).0" ;; \
	    patch) base="$$major.$$minor.$$((patch+1))" ;; \
	    *) echo "Unknown PART=$(PART) (use major|minor|patch)" >&2; exit 1 ;; \
	  esac; \
	  echo "$@: base $$base (PART=$(PART) from last stable $$last_stable)."; \
	fi; \
	if [ "$@" = "release-beta" ]; then \
	  n=$$(git tag --list "v$${base}-beta.*" | grep -oE '[0-9]+$$' | sort -n | tail -n1); \
	  if [ -z "$$n" ]; then n=0; fi; \
	  new="$${base}-beta.$$((n+1))"; \
	  echo "release-beta: -> pre-release $$new"; \
	  $(MAKE) --no-print-directory release VERSION="$$new"; \
	else \
	  echo "release-latest: -> stable $$base"; \
	  $(MAKE) --no-print-directory release VERSION="$$base"; \
	fi

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
