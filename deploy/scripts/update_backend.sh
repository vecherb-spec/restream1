#!/usr/bin/env bash
set -euo pipefail

# Backend-only deploy shortcut. Prefer deploy/scripts/deploy.sh for full releases.

REPO_DIR="${RESTREAM_REPO_DIR:-/opt/restream}"
BRANCH="${RESTREAM_DEPLOY_BRANCH:-cursor/restream-mvp-3225}"
SERVICE="${RESTREAM_BACKEND_SERVICE:-restream-backend}"
API_URL="${RESTREAM_API_URL:-http://127.0.0.1:8000/health}"

if [[ ! -d "$REPO_DIR/.git" ]]; then
  echo "Repository not found: $REPO_DIR"
  exit 1
fi

cd "$REPO_DIR"
git fetch origin
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"

if [[ -d .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
elif [[ -d venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source venv/bin/activate
fi

pip install -r requirements.txt -q
python3 -c "from database import init_db; init_db(); print('Database schema is up to date.')"

if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files "${SERVICE}.service" >/dev/null 2>&1; then
  systemctl restart "$SERVICE"
  systemctl is-active --quiet "$SERVICE"
  echo "Restarted systemd service: $SERVICE"
else
  kill $(pgrep -f "uvicorn main:app") 2>/dev/null || true
  nohup "$REPO_DIR/.venv/bin/uvicorn" main:app --host 127.0.0.1 --port 8000 > /var/log/restream-backend.log 2>&1 &
  echo "Backend restarted manually."
fi

curl -fsS "$API_URL" >/dev/null
echo "Backend updated. Health check passed."
