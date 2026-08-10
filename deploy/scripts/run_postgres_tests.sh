#!/usr/bin/env bash
set -euo pipefail

# Start ephemeral Postgres via Compose test overlay and run integration tests.
# Requires a working Docker daemon.
#
# On Debian/Ubuntu hosts with PEP 668, the script uses .venv-tests automatically.

REPO_DIR="${RESTREAM_REPO_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
cd "$REPO_DIR"

VENV_DIR="${RESTREAM_TEST_VENV:-$REPO_DIR/.venv-tests}"
PYTHON=python3

ensure_test_venv() {
  if [[ -x "$VENV_DIR/bin/python" ]]; then
    PYTHON="$VENV_DIR/bin/python"
  elif ! "$PYTHON" -c "import pytest, psycopg" >/dev/null 2>&1; then
    echo "Creating test virtualenv at $VENV_DIR ..."
    if ! "$PYTHON" -m venv "$VENV_DIR"; then
      echo "Failed to create venv. Install: apt install -y python3-venv python3-full" >&2
      exit 1
    fi
    PYTHON="$VENV_DIR/bin/python"
    "$PYTHON" -m pip install --upgrade pip
    "$PYTHON" -m pip install -r requirements.txt pytest
  fi

  if ! "$PYTHON" -c "import pytest" >/dev/null 2>&1; then
    echo "Installing pytest into $VENV_DIR ..."
    "$PYTHON" -m pip install pytest
  fi
  if ! "$PYTHON" -c "import psycopg" >/dev/null 2>&1; then
    echo "Installing psycopg into $VENV_DIR ..."
    "$PYTHON" -m pip install 'psycopg[binary]'
  fi
}

resolve_compose() {
  local docker_bin=()
  if ! command -v docker >/dev/null 2>&1; then
    echo "Docker CLI is not installed. Install: apt install -y docker.io docker-compose-v2" >&2
    exit 1
  fi

  if docker info >/dev/null 2>&1; then
    docker_bin=(docker)
  elif command -v sudo >/dev/null 2>&1 && sudo docker info >/dev/null 2>&1; then
    docker_bin=(sudo -E docker)
  else
    echo "Docker daemon unavailable. Start it: systemctl start docker" >&2
    exit 1
  fi

  if "${docker_bin[@]}" compose version >/dev/null 2>&1; then
    COMPOSE=("${docker_bin[@]}" compose -f docker-compose.yml -f docker-compose.test.yml)
    return
  fi

  # Legacy standalone binary (docker-compose v1).
  if command -v docker-compose >/dev/null 2>&1; then
    if [[ "${docker_bin[0]}" == "sudo" ]]; then
      COMPOSE=(sudo -E docker-compose -f docker-compose.yml -f docker-compose.test.yml)
    else
      COMPOSE=(docker-compose -f docker-compose.yml -f docker-compose.test.yml)
    fi
    return
  fi

  echo "Docker Compose is missing." >&2
  echo "Install one of:" >&2
  echo "  apt install -y docker-compose-v2" >&2
  echo "  apt install -y docker-compose" >&2
  echo "Then verify: docker compose version   OR   docker-compose version" >&2
  exit 1
}

ensure_test_venv
resolve_compose

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

echo "Using Python: $PYTHON"
echo "Using Compose: ${COMPOSE[*]}"
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

"$PYTHON" -m pytest -q tests/test_postgres_integration.py "$@"
echo "PostgreSQL integration tests finished."
