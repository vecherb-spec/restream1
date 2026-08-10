#!/usr/bin/env bash
set -euo pipefail

# Start ephemeral Postgres via Compose test overlay and run integration tests.
# Requires a working Docker daemon.

REPO_DIR="${RESTREAM_REPO_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
cd "$REPO_DIR"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker CLI is not installed. Install docker.io / docker-ce first." >&2
  exit 1
fi

if ! python3 -c "import pytest" >/dev/null 2>&1; then
  echo "pytest is missing for python3. Install test deps:" >&2
  echo "  python3 -m pip install -r requirements.txt pytest" >&2
  exit 1
fi

if ! python3 -c "import psycopg" >/dev/null 2>&1; then
  echo "psycopg is missing for python3. Install:" >&2
  echo "  python3 -m pip install 'psycopg[binary]'" >&2
  exit 1
fi

if docker info >/dev/null 2>&1; then
  DOCKER=(docker)
elif command -v sudo >/dev/null 2>&1 && sudo docker info >/dev/null 2>&1; then
  # Preserve env so Compose can interpolate POSTGRES_PASSWORD / DATABASE_URL.
  DOCKER=(sudo -E docker)
else
  echo "Docker daemon unavailable (cannot connect to docker socket)." >&2
  echo "Start it with: sudo systemctl start docker" >&2
  exit 1
fi

if ! "${DOCKER[@]}" compose version >/dev/null 2>&1; then
  echo "Docker Compose plugin missing. Install docker-compose-plugin / docker-compose-v2." >&2
  echo "Then run: docker compose version" >&2
  exit 1
fi

COMPOSE=("${DOCKER[@]}" compose -f docker-compose.yml -f docker-compose.test.yml)

export POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-restream_test_password}"
export DATABASE_URL="${DATABASE_URL:-postgresql://restream:restream_test_password@127.0.0.1:5432/restream_test}"
export RESTREAM_SRS_WEBHOOK_SECRET="${RESTREAM_SRS_WEBHOOK_SECRET:-unit-test-srs-secret-value-16}"
export NEXT_PUBLIC_OBS_SERVER_URL="${NEXT_PUBLIC_OBS_SERVER_URL:-rtmp://localhost/live}"
export NEXT_PUBLIC_HLS_BASE_URL="${NEXT_PUBLIC_HLS_BASE_URL:-http://localhost:8080/live}"

TEST_URL="${RESTREAM_TEST_DATABASE_URL:-postgresql://restream:restream_test_password@127.0.0.1:5432/restream_test}"

cleanup() {
  "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "Starting Postgres test container..."
"${COMPOSE[@]}" up -d postgres

echo "Waiting for Postgres health..."
ready=0
for _ in $(seq 1 60); do
  if "${COMPOSE[@]}" exec -T postgres pg_isready -U restream -d restream_test >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
if [[ "$ready" -ne 1 ]]; then
  echo "Postgres did not become ready in time" >&2
  "${COMPOSE[@]}" logs postgres >&2 || true
  exit 1
fi
"${COMPOSE[@]}" exec -T postgres pg_isready -U restream -d restream_test

echo "Running PostgreSQL integration tests..."
export RESTREAM_TEST_DATABASE_URL="$TEST_URL"
export RESTREAM_ALLOW_INSECURE_DEFAULTS=true
export RESTREAM_ADMIN_PASSWORD="${RESTREAM_ADMIN_PASSWORD:-test-admin-password-123}"
export RESTREAM_ALLOW_PLAINTEXT_PASSWORDS=false

python3 -m pytest -q tests/test_postgres_integration.py "$@"
echo "PostgreSQL integration tests finished."
