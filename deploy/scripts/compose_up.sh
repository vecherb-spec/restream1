#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${RESTREAM_REPO_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
cd "$REPO_DIR"

if [[ ! -f .env ]]; then
  echo "Missing .env — copy .env.example and set required secrets first" >&2
  exit 1
fi

# shellcheck disable=SC1091
set -a
source <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' .env | sed 's/\r$//')
set +a

: "${RESTREAM_SRS_WEBHOOK_SECRET:?RESTREAM_SRS_WEBHOOK_SECRET is required in .env}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required in .env}"
: "${DATABASE_URL:?DATABASE_URL is required in .env}"

export RESTREAM_SRS_BACKEND_HOOK="${RESTREAM_SRS_BACKEND_HOOK:-backend:8000}"
./deploy/scripts/render_srs_conf.sh
docker compose "$@" up -d --build
echo "Compose is up. Backend/API bound to 127.0.0.1:8000; RTMP on 1935."
