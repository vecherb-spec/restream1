#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${RESTREAM_REPO_DIR:-/opt/restream}"
ENV_FILE="$REPO_DIR/.env"
BRANCH="${RESTREAM_DEPLOY_BRANCH:-cursor/restream-mvp-3225}"

echo "=========================================="
echo "  Restream recovery (HTTP 500 fix)"
echo "=========================================="

cd "$REPO_DIR"

echo
echo "[1/8] Update code..."
git fetch origin
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"

echo
echo "[2/8] Python dependencies..."
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

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[FAIL] No $ENV_FILE — create it from .env.example first"
  exit 1
fi

echo
echo "[3/8] Find database file..."
FOUND_DB=""
for candidate in \
  "$REPO_DIR/restream.db" \
  "/opt/restream/restream.db" \
  "/data/restream.db" \
  "$(grep '^RESTREAM_DB_PATH=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2-)"; do
  if [[ -n "$candidate" && -f "$candidate" ]]; then
    FOUND_DB="$candidate"
    break
  fi
done

if [[ -z "$FOUND_DB" ]]; then
  echo "[FAIL] restream.db not found. Search:"
  find /opt /data /root -name 'restream.db' 2>/dev/null | head -5
  exit 1
fi
echo "[OK] Database: $FOUND_DB"

echo
echo "[4/8] Fix .env database settings..."
# Never auto-clear DATABASE_URL just because localhost:5432 is closed.
# Remote/managed Postgres is common; clearing it can silently switch production to SQLite.
if grep -qE '^DATABASE_URL=(postgresql|postgres)://' "$ENV_FILE"; then
  DB_URL="$(grep -E '^DATABASE_URL=(postgresql|postgres)://' "$ENV_FILE" | head -1 | cut -d= -f2-)"
  echo "[OK] Keeping DATABASE_URL (Postgres mode). Will validate via Python next."
  echo "     $DB_URL"
else
  if grep -q '^RESTREAM_DB_PATH=' "$ENV_FILE"; then
    sed -i "s|^RESTREAM_DB_PATH=.*|RESTREAM_DB_PATH=$FOUND_DB|" "$ENV_FILE"
  else
    echo "RESTREAM_DB_PATH=$FOUND_DB" >> "$ENV_FILE"
  fi
  echo "[OK] RESTREAM_DB_PATH=$FOUND_DB"
fi

echo
echo "[5/8] Test database from Python..."
python3 << PY
import os
from pathlib import Path
os.chdir("$REPO_DIR")
# reload env
for line in Path(".env").read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    os.environ[k.strip()] = v.strip().strip('"').strip("'")

import importlib
import database
importlib.reload(database)
ok, detail = database.check_database()
print("check_database:", ok, detail)
if not ok:
    raise SystemExit(1)
database.init_db()
print("init_db: OK")
PY

echo
echo "[6/8] Restart backend..."
if systemctl list-unit-files restream-backend.service >/dev/null 2>&1; then
  systemctl restart restream-backend
  sleep 2
  systemctl is-active restream-backend && echo "[OK] restream-backend active" || {
    echo "[FAIL] restream-backend not active"
    journalctl -u restream-backend -n 20 --no-pager
    exit 1
  }
else
  kill $(pgrep -f "uvicorn main:app") 2>/dev/null || true
  sleep 1
  nohup "$REPO_DIR/.venv/bin/uvicorn" main:app --host 127.0.0.1 --port 8000 > /var/log/restream-backend.log 2>&1 &
  sleep 2
fi

echo
echo "[7/8] API checks..."
HEALTH=$(curl -sS --max-time 5 http://127.0.0.1:8000/health || echo FAIL)
echo "health: $HEALTH"

LOGIN_CODE=$(curl -sS --max-time 5 -o /tmp/restream-login.json -w "%{http_code}" \
  -X POST http://127.0.0.1:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"test","password":"test"}')
echo "login HTTP: $LOGIN_CODE (need 401, not 500)"
cat /tmp/restream-login.json 2>/dev/null || true
echo

if [[ "$LOGIN_CODE" == "500" ]]; then
  echo "[FAIL] Still HTTP 500. Last backend log lines:"
  journalctl -u restream-backend -n 30 --no-pager 2>/dev/null || tail -30 /var/log/restream-backend.log
  exit 1
fi

echo
echo "[8/8] Rebuild frontend (optional)..."
cd "$REPO_DIR/frontend"
npm install --silent 2>/dev/null || npm install
npm run build
if systemctl list-unit-files restream-frontend.service >/dev/null 2>&1; then
  systemctl restart restream-frontend
fi

echo
echo "=========================================="
echo "  DONE. Try login on https://restream.medialive.ru"
echo "=========================================="
