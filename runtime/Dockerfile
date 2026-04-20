# ---- Base image: browser automation runtime for OpenClaw ----
#
# Multi-stage build:
#   base    – used directly by local (docker compose)
#   staging – adds the staging entrypoint (fly.io)
#
# To update: check https://github.com/openclaw/openclaw/pkgs/container/openclaw/versions
FROM ghcr.io/openclaw/openclaw:2026.4.15 AS base

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
RUN mkdir -p /opt/config-bundle /opt/openclaw-config /opt/openclaw-workspaces /opt/openclaw-plugins \
    && chown -R node:node /opt/config-bundle /opt/openclaw-config /opt/openclaw-workspaces /opt/openclaw-plugins

# SellerClaw web UI chat delivery channel (OpenClaw plugin).
# Files must stay root-owned: OpenClaw blocks config-origin plugins with non-root ownership.
COPY sellerclaw-agent/plugins/sellerclaw-ui /opt/openclaw-plugins/sellerclaw-ui

# Workaround: openclaw 2026.4.12 is missing dist/subagent-registry.runtime.js
# (upstream bug https://github.com/openclaw/openclaw/issues/66096).
# The code dynamically imports this module for subagent cleanup; without it every
# cleanup pass logs ERR_MODULE_NOT_FOUND.  The no-op shim makes the import succeed
# and the caller exits early via `if (!engine.onSubagentEnded) return`.
# TODO: remove once we upgrade to a release that includes the fix.
RUN printf '%s\n' \
      'export function ensureRuntimePluginsLoaded() {}' \
      'export function ensureContextEnginesInitialized() {}' \
      'export async function resolveContextEngine() { return {}; }' \
      > /app/dist/subagent-registry.runtime.js

# Remove unused bundled extensions from the base image to speed up cold start.
# OpenClaw scans all directories in the bundled extensions dir at startup, even when
# allow/load filters are set. Pruning 90+ unused extensions (~119 MB) avoids that overhead.
# Keep only extensions referenced by the generated openclaw.json:
#   browser, telegram, litellm, brave, tavily, memory-core
RUN cd /app/dist/extensions && \
    for dir in */; do \
        case "${dir%/}" in \
            browser|telegram|litellm|brave|tavily|memory-core) ;; \
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
    /usr/local/bin/openclaw_tcp_check

EXPOSE 7788 6080 6081 8001

CMD ["supervisord", "-n", "-c", "/etc/supervisor/conf.d/openclaw.conf"]


# ---- Admin UI (served by agent FastAPI from /admin) ----
FROM node:20-alpine AS ui-build
WORKDIR /ui
COPY sellerclaw-agent/admin-ui/package.json sellerclaw-agent/admin-ui/package-lock.json* ./
RUN npm install
COPY sellerclaw-agent/admin-ui/ ./
RUN npm run build


# ---- Staging: SellerClaw agent API + OpenClaw runtime in one image ----
FROM base AS staging

COPY --from=ghcr.io/astral-sh/uv:0.9.18 /uv /usr/local/bin/uv
COPY sellerclaw-agent/pyproject.toml sellerclaw-agent/uv.lock /tmp/proj/
# Debian bookworm Python is PEP 668 "externally managed"; allow system install in this image only.
# Resolve the server-runtime dependency set from uv.lock (single source of truth: pyproject.toml),
# then install it system-wide. No editable project, no dev/CLI extras — only what the API server needs.
RUN cd /tmp/proj \
    && UV_BREAK_SYSTEM_PACKAGES=1 uv export \
        --frozen --no-dev --no-emit-project --extra server --no-hashes \
        -o /tmp/requirements.txt \
    && UV_BREAK_SYSTEM_PACKAGES=1 uv pip install --system --no-cache -r /tmp/requirements.txt \
    && rm -rf /tmp/proj /tmp/requirements.txt

COPY sellerclaw-agent/sellerclaw_agent/ /app/sellerclaw_agent/
COPY sellerclaw-agent/agent_resources/ /app/agent_resources/
COPY --from=ui-build /ui/dist /app/admin-ui/dist

ENV AGENT_ADMIN_UI_DIST=/app/admin-ui/dist
ENV OPENCLAW_BUNDLE_VOLUME_PATH=/opt/config-bundle
ENV SELLERCLAW_EDGE_PING=1
ENV OPENCLAW_GATEWAY_HTTP_BASE=http://127.0.0.1:7789

RUN chown -R node:node /app/sellerclaw_agent /app/agent_resources /app/admin-ui

COPY sellerclaw-agent/runtime/supervisord-staging.conf /etc/supervisor/conf.d/openclaw.conf

COPY sellerclaw-agent/runtime/entrypoint-staging.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
