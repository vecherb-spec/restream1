#!/usr/bin/env bash
set -euo pipefail

# Full production deploy: pull code, install deps, build frontend, restart services.

REPO_DIR="${RESTREAM_REPO_DIR:-/opt/restream}"
BRANCH="${RESTREAM_DEPLOY_BRANCH:-cursor/restream-mvp-3225}"

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
else
  python3 -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

pip install -r requirements.txt -q
mkdir -p "$REPO_DIR/logs" "$REPO_DIR/state" "$REPO_DIR/backups"
python3 -c "from database import init_db; init_db(); print('Database schema is up to date.')"

cd frontend
npm install
npm run build
cd ..

if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files restream-backend.service >/dev/null 2>&1; then
  systemctl restart restream-backend restream-frontend
  echo "Restarted systemd services: restream-backend, restream-frontend"
elif command -v systemctl >/dev/null 2>&1; then
  echo "Systemd services are not installed yet."
  echo "Run: chmod +x deploy/scripts/install_systemd.sh && ./deploy/scripts/install_systemd.sh"
  kill $(pgrep -f "uvicorn main:app") 2>/dev/null || true
  nohup "$REPO_DIR/.venv/bin/uvicorn" main:app --host 127.0.0.1 --port 8000 > /var/log/restream-backend.log 2>&1 &
  echo "Backend restarted manually."
else
  kill $(pgrep -f "uvicorn main:app") 2>/dev/null || true
  nohup "$REPO_DIR/.venv/bin/uvicorn" main:app --host 127.0.0.1 --port 8000 > /var/log/restream-backend.log 2>&1 &
  echo "Backend restarted manually."
fi

"$REPO_DIR/deploy/scripts/healthcheck.sh"
echo "Deploy finished."
