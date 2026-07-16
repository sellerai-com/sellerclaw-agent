# ---- Base image: browser automation runtime for OpenClaw ----
#
# Multi-stage build:
#   base    – used directly by local (docker compose)
#   staging – adds the staging entrypoint (fly.io)
#
# To update: check https://github.com/openclaw/openclaw/pkgs/container/openclaw/versions
# Override either ARG from docker-compose to test against a different version.
#
# OPENCLAW_REPO:
#   Default = upstream ghcr.io/openclaw/openclaw (= what `openclaw doctor` ships).
#   Override to ghcr.io/sellerai-com/openclaw-pinned when consuming our own
#   pinned builds (see runtime/build-openclaw-pinned.sh and the
#   `.github/workflows/openclaw-pinned.yml` CI workflow).
#
# OPENCLAW_TAG:
#   Default = pinned upstream release.
#   Override to `pinned-<sha10>` for our reproducible from-source builds.
#
# WHY pinned builds: ghcr.io's `:main` tag on the upstream repo lags by weeks
# (snapshot from 2026-03-26 at time of writing) and fails our config schema
# validation. To consume an unreleased upstream commit (e.g. PR #84006
# generated-media direct fallback, merged 2026-05-22), use the from-source
# workflow above.
ARG OPENCLAW_REPO=ghcr.io/openclaw/openclaw
ARG OPENCLAW_TAG=2026.7.1
FROM ${OPENCLAW_REPO}:${OPENCLAW_TAG} AS base

ENV OPENCLAW_PORT=7788
ENV OPENCLAW_CONFIG_PATH=/home/node/.openclaw/openclaw.json
ENV DISPLAY=:1
# Line-buffered Python stdout/stderr in Docker (diagnostics + session tail).
ENV PYTHONUNBUFFERED=1
# OpenClaw runtime tuning lives here (not in sellerclaw control plane).
# Operators can override per deployment via Fly app config / compose env.
# 2560 MB covers ~1.6 GB agent-bootstrap RSS with headroom (see openclaw_start).
ENV OPENCLAW_NODE_MAX_OLD_SPACE_SIZE_MB=2560
# Extra cgroup startup logging; default off, turn on for capacity debugging.
ENV OPENCLAW_DIAGNOSTICS=0

USER root

# KasmVNC (combined X server + VNC + web client), browser, gost proxy,
# nginx reverse proxy, supervisor process manager, fonts.
# Architecture-aware: uses TARGETARCH (amd64 | arm64) provided by BuildKit.
ARG TARGETARCH

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        fonts-liberation \
        fonts-noto-color-emoji \
        gettext-base \
        jq \
        nginx \
        python3 \
        python3-bs4 \
        supervisor \
        wget \
    && ln -sf /usr/bin/python3 /usr/bin/python \
    && if [ "$TARGETARCH" = "arm64" ]; then \
        apt-get install -y --no-install-recommends chromium; \
    else \
        wget -q -O /tmp/google-chrome.deb \
            https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
        && apt-get install -y /tmp/google-chrome.deb \
        && rm -f /tmp/google-chrome.deb; \
    fi \
    && wget -q -O /tmp/kasmvnc.deb \
        "https://github.com/kasmtech/KasmVNC/releases/download/v1.4.0/kasmvncserver_bookworm_1.4.0_${TARGETARCH}.deb" \
    && apt-get install -y /tmp/kasmvnc.deb \
    && rm -f /tmp/kasmvnc.deb \
    && adduser node ssl-cert \
    && rm -rf /var/lib/apt/lists/*

# Playwright is required for browser tool internals and CDP flows.
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
ENV NODE_PATH=/usr/local/lib/node_modules
RUN npm install -g playwright

# gost proxy forwarder.
RUN wget -qO- "https://github.com/go-gost/gost/releases/download/v3.2.6/gost_3.2.6_linux_${TARGETARCH}.tar.gz" \
    | tar -xz -C /usr/local/bin gost \
    && chmod +x /usr/local/bin/gost

# Writable runtime directories for the node user.
# /home/node/.config must be node-owned: the agent server (uvicorn, runs as node)
# mirrors the sca_ agent token into ~/.config/sellerclaw/config.toml at startup via
# write_cli_config(). If .config is root-owned, that write fails with EACCES, the
# sellerclaw CLI has no token, and every agent CLI call gets `401 Missing bearer token`
# — which the agent then misreports to users (e.g. "No stores are connected").
RUN mkdir -p /opt/config-bundle /opt/openclaw-config /opt/openclaw-workspaces /opt/openclaw-plugins /home/node/.config \
    && chown -R node:node /opt/config-bundle /opt/openclaw-config /opt/openclaw-workspaces /opt/openclaw-plugins /home/node/.config

# SellerClaw web UI chat delivery channel (OpenClaw plugin).
# Files must stay root-owned: OpenClaw blocks config-origin plugins with non-root ownership.
COPY sellerclaw-agent/plugins/sellerclaw-ui /opt/openclaw-plugins/sellerclaw-ui
COPY sellerclaw-agent/plugins/sellerclaw-web-search /opt/openclaw-plugins/sellerclaw-web-search

# Long-term memory: the official mem0 OpenClaw plugin, used in PLATFORM mode pointed at the cloud's
# Mem0-compatible adapter (config_generator enables it only when the manifest's memory block is on).
# Vendor the package here (its openclaw.plugin.json must sit at the dir root) and install deps in-image
# so the transitive native better-sqlite3 binding matches TARGETARCH — even though platform mode never
# touches the local SQLite store. Must stay root-owned (OpenClaw rejects config-origin plugins with
# non-root ownership).
# This directory is NOT referenced via plugins.load.paths. Instead openclaw_start registers it into
# OpenClaw's plugin registry at boot (`openclaw plugins install <dir>`, offline) so the gateway's
# startup doctor treats the memory slot as installed and does not re-download it from npm every cold
# start (managed machines are volumeless, so the registry is wiped each boot).
# Pin exact version so image rebuilds are reproducible (check: npm view @mem0/openclaw-mem0 version).
ARG MEM0_OPENCLAW_PLUGIN_VERSION=1.0.13
RUN mkdir -p /opt/openclaw-plugins/openclaw-mem0 \
    && cd /opt/openclaw-plugins/openclaw-mem0 \
    && npm pack "@mem0/openclaw-mem0@${MEM0_OPENCLAW_PLUGIN_VERSION}" \
    && tar --strip-components=1 -xzf mem0-openclaw-mem0-*.tgz \
    && rm -f mem0-openclaw-mem0-*.tgz \
    && npm install --omit=dev --no-audit --no-fund \
    && chown -R root:root /opt/openclaw-plugins/openclaw-mem0


# Remove unused bundled extensions from the base image to speed up cold start.
# OpenClaw scans all directories in the bundled extensions dir at startup, even when
# allow/load filters are set. Pruning 90+ unused extensions (~119 MB) avoids that overhead.
# Keep only extensions referenced by the generated openclaw.json. This list must stay in
# sync with every provider used in an agent model ref (models.providers / agents.*.model)
# and every bundled plugin the config enables (plugins.allow + plugins.entries):
#   browser, telegram, whatsapp, litellm, memory-core -> chat + tools + memory
#   anthropic, google                         -> back the `anthropic`/`google` providers
#                                                (model refs anthropic/claude-*, google/veo-*)
#   document-extract                          -> bundled plugin enabled for the PDF tool
# (web search is sellerclaw-web-search, an external plugin under /opt/openclaw-plugins.)
# whatsapp backs the personal-account WhatsApp channel (Baileys + QR pairing); it activates
# only when an agent's generated config has channels.whatsapp.enabled. Its baileys /
# whatsapp-rust-bridge deps live in node_modules (not pruned here), so keeping the extension
# dir is enough. Pairing writes the session to OpenClaw's default whatsapp authDir, which must
# sit on a persistent volume so the link survives container restarts.
# Each provider/plugin extension registers itself at extension-load time; pruning it makes
# OpenClaw fail to resolve that provider's API or plugin even though the config references
# it — e.g. the google prune surfaced as `No video-generation provider registered for google`.
RUN cd /app/dist/extensions && \
    for dir in */; do \
        case "${dir%/}" in \
            browser|telegram|whatsapp|litellm|memory-core|anthropic|google|document-extract) ;; \
            *) rm -rf "$dir" ;; \
        esac; \
    done

# KasmVNC server-wide configuration (HTTP mode, no SSL, port 6080).
COPY sellerclaw-agent/runtime/kasmvnc/kasmvnc.yaml /etc/kasmvnc/kasmvnc.yaml

# Runtime scripts and configs.
COPY sellerclaw-agent/runtime/commands/openclaw_diagnostics /usr/lib/python3/dist-packages/openclaw_diagnostics

COPY sellerclaw-agent/runtime/commands/openclaw_start              /usr/local/bin/openclaw_start
COPY sellerclaw-agent/runtime/commands/openclaw_diagnose_startup   /usr/local/bin/openclaw_diagnose_startup
COPY sellerclaw-agent/runtime/commands/fetch_config_bundle         /usr/local/bin/fetch_config_bundle
COPY sellerclaw-agent/runtime/commands/gost_start                  /usr/local/bin/gost_start
COPY sellerclaw-agent/runtime/commands/openclaw_chrome             /usr/local/bin/openclaw_chrome
COPY sellerclaw-agent/runtime/commands/kasmvnc_start               /usr/local/bin/kasmvnc_start
# Safe archive extraction. Agents must use this instead of `unzip` (see the file_storage skill):
# it refuses symlinks, path escapes and decompression bombs while unpacking, not just from the
# zip's (forgeable) table of contents.
COPY sellerclaw-agent/runtime/commands/sellerclaw_unzip            /usr/local/bin/sellerclaw_unzip
COPY sellerclaw-agent/runtime/nginx/openclaw-proxy.conf            /etc/nginx/openclaw-proxy.conf
COPY sellerclaw-agent/runtime/healthchecks/openclaw_tcp_check      /usr/local/bin/openclaw_tcp_check
COPY sellerclaw-agent/runtime/supervisord.conf                     /etc/supervisor/conf.d/openclaw.conf

RUN chmod +x \
    /usr/local/bin/openclaw_start \
    /usr/local/bin/openclaw_diagnose_startup \
    /usr/local/bin/fetch_config_bundle \
    /usr/local/bin/gost_start \
    /usr/local/bin/openclaw_chrome \
    /usr/local/bin/kasmvnc_start \
    /usr/local/bin/sellerclaw_unzip \
    /usr/local/bin/openclaw_tcp_check

EXPOSE 7788 6080 6081 8001

CMD ["supervisord", "-n", "-c", "/etc/supervisor/conf.d/openclaw.conf"]


# ---- Admin UI (served by agent FastAPI from /admin) ----
FROM node:24-alpine AS ui-build
WORKDIR /ui
COPY sellerclaw-agent/admin-ui/package.json sellerclaw-agent/admin-ui/package-lock.json* ./
RUN npm install
COPY sellerclaw-agent/admin-ui/ ./
RUN npm run build


# ---- Staging: SellerClaw agent API + OpenClaw runtime in one image ----
FROM base AS staging

# Agent self-reported version (sent to sellerclaw on connect/ping).
# Computed from `git describe --tags` by setup.sh / Makefile / CI and forwarded
# here as a build-arg; baked into ENV so the image carries its own version
# even when started via plain `docker run` without an env-file.
ARG SELLERCLAW_AGENT_VERSION=0.0.0+unknown
ENV SELLERCLAW_AGENT_VERSION=${SELLERCLAW_AGENT_VERSION}

# Underlying OpenClaw runtime version (the base image tag). Re-declare the
# pre-FROM build-arg so this stage can bake it into ENV; reported to sellerclaw
# on every ping and surfaced in the agent-info UI.
ARG OPENCLAW_TAG
ENV OPENCLAW_VERSION=${OPENCLAW_TAG}

COPY --from=ghcr.io/astral-sh/uv:0.9.18 /uv /usr/local/bin/uv
COPY sellerclaw-agent/pyproject.toml sellerclaw-agent/uv.lock /tmp/proj/
# Debian bookworm Python is PEP 668 "externally managed"; allow system install in this image only.
# Resolve the server-runtime dependency set from uv.lock (single source of truth: pyproject.toml),
# then install it system-wide. No editable project, no dev deps, only the `server` extra. This still
# pulls the core deps (incl. rich/questionary, needed by the CLI entry point) — a few MB the API
# server itself does not import; kept for a single, simple dependency set over a leaner image.
RUN cd /tmp/proj \
    && UV_BREAK_SYSTEM_PACKAGES=1 uv export \
        --frozen --no-dev --no-emit-project --extra server --no-hashes \
        -o /tmp/requirements.txt \
    && UV_BREAK_SYSTEM_PACKAGES=1 uv pip install --system --no-cache -r /tmp/requirements.txt \
    && rm -rf /tmp/proj /tmp/requirements.txt

# Edge-side Python CLIs (e.g. sellerclaw-cli) the OpenClaw agent invokes via exec-tool;
# version pins live in runtime/edge-requirements.txt (kept in lockstep with the cloud
# SELLERCLAW_CLI_VERSION). Installed AFTER the server-deps step above so the pinned CLI is the
# LAST writer and survives into the shipped image: the server-deps resolve from uv.lock, and a
# stray sellerclaw-cli pin there silently clobbered this edge pin in an earlier layer (how the
# 0.53.0/0.54.0 images shipped CLI 0.23.0). The build then FAILS unless the `sellerclaw` on PATH
# equals the pin, so a downgrade in the shipped stage is loud at build time, not silent at runtime.
# (uv is already on PATH from the server-deps step; this runs before the local-wheel override
# below, so a contributor's SELLERCLAW_CLI_SOURCE=local wheel still wins.)
#
# Auth for sellerclaw-cli at runtime: SELLERCLAW_API_URL comes from docker-compose/fly env;
# SELLERCLAW_TOKEN is hydrated by openclaw_start from the device-flow token file.
COPY sellerclaw-agent/runtime/edge-requirements.txt /tmp/edge-requirements.txt
RUN UV_BREAK_SYSTEM_PACKAGES=1 uv pip install --system --no-cache -r /tmp/edge-requirements.txt \
    && pinned_cli="$(grep '^sellerclaw-cli' /tmp/edge-requirements.txt | sed 's/.*==//' | tr -d '[:space:]')" \
    && installed_cli="$(sellerclaw --version | tr -d '[:space:]')" \
    && if [ "$pinned_cli" != "$installed_cli" ]; then \
         echo "FATAL: sellerclaw-cli pinned ${pinned_cli} but installed ${installed_cli}" >&2; \
         exit 1; \
       fi \
    && rm -f /tmp/edge-requirements.txt

# Developer-only sellerclaw-cli override.
#
# Default (SELLERCLAW_CLI_SOURCE=pypi): the edge `sellerclaw` command and the
# `sellerclaw_cli` library come from PyPI, exactly as in production / CI — this
# block is a no-op. This is the path users and the GHCR build always take.
#
# When a contributor sets SELLERCLAW_CLI_SOURCE=local (driven by the Makefile +
# gitignored dev.env), a wheel built from their local sellerclaw-cli checkout is
# staged into runtime/.local-wheels/ and reinstalled system-wide here — AFTER the
# server-deps install above, so the local build wins over whatever uv.lock pinned.
# The directory is committed empty (.gitkeep) so the COPY always succeeds, and CI
# never sets the arg, so the wheel (if any) is ignored.
ARG SELLERCLAW_CLI_SOURCE=pypi
COPY sellerclaw-agent/runtime/.local-wheels/ /tmp/local-wheels/
RUN if [ "$SELLERCLAW_CLI_SOURCE" = "local" ]; then \
        wheel="$(ls /tmp/local-wheels/*.whl 2>/dev/null | head -n1)"; \
        if [ -z "$wheel" ]; then \
            echo "SELLERCLAW_CLI_SOURCE=local but no wheel in runtime/.local-wheels/ (run 'make' which stages it from dev.env)" >&2; \
            exit 1; \
        fi; \
        echo "Overriding sellerclaw-cli with local wheel: $wheel"; \
        UV_BREAK_SYSTEM_PACKAGES=1 uv pip install --system --no-cache --reinstall-package sellerclaw-cli "$wheel"; \
    fi \
    && rm -rf /tmp/local-wheels

COPY sellerclaw-agent/sellerclaw_agent/ /app/sellerclaw_agent/
COPY --from=ui-build /ui/dist /app/admin-ui/dist

ENV AGENT_ADMIN_UI_DIST=/app/admin-ui/dist
ENV OPENCLAW_BUNDLE_VOLUME_PATH=/opt/config-bundle
ENV SELLERCLAW_EDGE_PING=1
ENV OPENCLAW_GATEWAY_HTTP_BASE=http://127.0.0.1:7789

RUN chown -R node:node /app/sellerclaw_agent /app/admin-ui

COPY sellerclaw-agent/runtime/supervisord-staging.conf /etc/supervisor/conf.d/openclaw.conf

COPY sellerclaw-agent/runtime/entrypoint-staging.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Override the openclaw base image healthcheck (which probes port 18789 that we do
# not run): check the FastAPI control plane that /entrypoint.sh brings up on 8001.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8001/health || exit 1

ENTRYPOINT ["/entrypoint.sh"]
