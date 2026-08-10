#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${RESTREAM_REPO_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
cd "$REPO_DIR"

export RESTREAM_SRS_BACKEND_HOOK="${RESTREAM_SRS_BACKEND_HOOK:-backend:8000}"
./deploy/scripts/render_srs_conf.sh
docker compose "$@" up -d --build
echo "Compose is up. SRS webhook hook: $RESTREAM_SRS_BACKEND_HOOK"
