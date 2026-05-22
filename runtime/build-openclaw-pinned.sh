#!/usr/bin/env bash
# Build a reproducible OpenClaw runtime image from a specific upstream commit.
#
# Why: ghcr.io publishes only release tags (e.g. 2026.5.20) and a single,
# rarely-refreshed `main` snapshot. When we need an unreleased upstream fix
# (e.g. PR #84006 generated-media direct fallback merged 2026-05-22), neither
# `:main` nor `:latest` will carry it for days. This script clones openclaw at
# a chosen commit SHA, runs upstream's own multi-stage Dockerfile, and tags
# the result so our docker-compose build picks it up via OPENCLAW_TAG.
#
# Usage:
#   ./runtime/build-openclaw-pinned.sh [commit-sha] [--push]
#
# Examples:
#   ./runtime/build-openclaw-pinned.sh                              # default commit, local only
#   ./runtime/build-openclaw-pinned.sh 37a9f58d1b                   # explicit commit, local only
#   ./runtime/build-openclaw-pinned.sh 37a9f58d1b --push            # build AND push to GHCR
#
# Environment overrides:
#   OPENCLAW_PINNED_REPO   Registry repo to tag/push into.
#                          Default: ghcr.io/sellerai-com/openclaw-pinned
#
# Before --push works you must `docker login ghcr.io -u <gh-user>` with a PAT
# that has `write:packages`. The CI workflow at
# `.github/workflows/openclaw-pinned.yml` does the same thing without needing
# a local login.
#
# Build is heavy (~10-20 minutes, ~3-5 GB RAM). pnpm/bun caches are reused
# via BuildKit cache mounts, so subsequent builds of nearby commits are fast.
set -euo pipefail

DEFAULT_COMMIT="37a9f58d1b10341159009b3b492c0434df1d2630"  # main @ 2026-05-22, PR #84006 (media completion direct fallback)
TARGET_REPO="${OPENCLAW_PINNED_REPO:-ghcr.io/sellerai-com/openclaw-pinned}"

COMMIT=""
PUSH=0
for arg in "$@"; do
    case "$arg" in
        --push) PUSH=1 ;;
        -h|--help)
            sed -n '2,30p' "$0"
            exit 0
            ;;
        -*)
            echo "ERROR: unknown flag '$arg'" >&2
            exit 1
            ;;
        *)
            if [[ -n "$COMMIT" ]]; then
                echo "ERROR: more than one positional argument" >&2
                exit 1
            fi
            COMMIT="$arg"
            ;;
    esac
done
COMMIT="${COMMIT:-$DEFAULT_COMMIT}"

if ! [[ "$COMMIT" =~ ^[0-9a-f]{7,40}$ ]]; then
    echo "ERROR: '$COMMIT' is not a valid git SHA (need 7-40 hex chars)" >&2
    exit 1
fi

SHORT="${COMMIT:0:10}"
TAG="${TARGET_REPO}:pinned-${SHORT}"
WORKDIR="$(mktemp -d -t openclaw-pinned.XXXXXX)"
trap 'rm -rf "$WORKDIR"' EXIT

echo "==> Cloning openclaw @ ${COMMIT}"
git clone --quiet --filter=blob:none --no-checkout https://github.com/openclaw/openclaw.git "$WORKDIR/openclaw"
cd "$WORKDIR/openclaw"
git fetch --quiet --depth=1 origin "$COMMIT"
git checkout --quiet "$COMMIT"

ACTUAL_SHA="$(git rev-parse HEAD)"
ACTUAL_DATE="$(git show -s --format=%ci HEAD)"
echo "==> Checked out ${ACTUAL_SHA} (${ACTUAL_DATE})"

echo "==> Building ${TAG} (this takes ~10-20 minutes)"
DOCKER_BUILDKIT=1 docker build \
    --pull \
    --tag "$TAG" \
    --label "org.opencontainers.image.revision=${ACTUAL_SHA}" \
    --label "org.opencontainers.image.created=${ACTUAL_DATE}" \
    --label "org.opencontainers.image.source=https://github.com/openclaw/openclaw" \
    .

if [[ "$PUSH" -eq 1 ]]; then
    echo "==> Pushing ${TAG}"
    docker push "$TAG"
fi

echo
echo "==> Done."
echo "    Image:  ${TAG}"
echo "    Commit: ${ACTUAL_SHA}"
if [[ "$PUSH" -eq 1 ]]; then
    echo "    Pushed: yes"
else
    echo "    Pushed: no (run again with --push, or pull from CI)"
fi
echo
echo "    Next steps:"
echo "      1. set 'OPENCLAW_REPO=${TARGET_REPO}' in .env.local"
echo "      2. set 'OPENCLAW_TAG=pinned-${SHORT}' in .env.local"
echo "      3. docker compose --env-file .env.local build server"
echo "      4. docker compose --env-file .env.local up -d server"
