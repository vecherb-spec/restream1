#!/usr/bin/env bash
set -euo pipefail

# Pull the latest backend code and restart the API service.
# Run on the production host after merging password recovery changes.

REPO_DIR="${RESTREAM_REPO_DIR:-/opt/restream}"
BRANCH="${RESTREAM_DEPLOY_BRANCH:-main}"
SERVICE="${RESTREAM_BACKEND_SERVICE:-restream-backend}"
API_URL="${RESTREAM_API_URL:-http://127.0.0.1:8000}"

if [[ ! -d "$REPO_DIR/.git" ]]; then
  echo "Repository not found: $REPO_DIR"
  echo "Set RESTREAM_REPO_DIR to your checkout path."
  exit 1
fi

cd "$REPO_DIR"
git fetch origin
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"

if [[ -d venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source venv/bin/activate
  pip install -r requirements.txt -q
elif [[ -d .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install -r requirements.txt -q
fi

python3 -c "from database import init_db; init_db(); print('Database schema is up to date.')"

if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files "${SERVICE}.service" >/dev/null 2>&1; then
  sudo systemctl restart "$SERVICE"
  sudo systemctl is-active --quiet "$SERVICE"
  echo "Restarted systemd service: $SERVICE"
elif command -v docker >/dev/null 2>&1 && docker compose ps backend >/dev/null 2>&1; then
  docker compose up -d --build backend
  echo "Rebuilt docker compose service: backend"
else
  echo "Restart backend manually, then re-run healthcheck.sh"
fi

response="$(curl -fsS -X POST "${API_URL%/}/api/auth/forgot-password" \
  -H "Content-Type: application/json" \
  -d '{"identifier":"healthcheck"}')"
if ! grep -q '"code":0' <<<"$response"; then
  echo "Password reset API check failed: $response"
  exit 1
fi

echo "Backend updated. Password reset API is available."
