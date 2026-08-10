#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${RESTREAM_REPO_DIR:-/opt/restream}"
ENV_FILE="$REPO_DIR/.env"
DB_CANDIDATES=(
  "$REPO_DIR/restream.db"
  "/opt/restream/restream.db"
  "/data/restream.db"
)

echo "== Restream database recovery =="

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE"
  exit 1
fi

if grep -q '^DATABASE_URL=postgresql' "$ENV_FILE" || grep -q '^DATABASE_URL=postgres' "$ENV_FILE"; then
  echo "[WARN] DATABASE_URL points to PostgreSQL."
  echo "       If PostgreSQL is not running on this server, login will return HTTP 500."
  echo "       For SQLite production, set: DATABASE_URL="
fi

FOUND_DB=""
for candidate in "${DB_CANDIDATES[@]}"; do
  if [[ -f "$candidate" ]]; then
    FOUND_DB="$candidate"
    break
  fi
done

if [[ -z "$FOUND_DB" ]]; then
  echo "[FAIL] Could not find restream.db in common locations."
  exit 1
fi

echo "[OK] Found database: $FOUND_DB"

if ! grep -q '^RESTREAM_DB_PATH=' "$ENV_FILE"; then
  echo "RESTREAM_DB_PATH=$FOUND_DB" >> "$ENV_FILE"
  echo "[FIX] Added RESTREAM_DB_PATH=$FOUND_DB"
else
  CURRENT_PATH="$(grep '^RESTREAM_DB_PATH=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
  if [[ "$CURRENT_PATH" != "$FOUND_DB" && ! -f "$CURRENT_PATH" ]]; then
    sed -i "s|^RESTREAM_DB_PATH=.*|RESTREAM_DB_PATH=$FOUND_DB|" "$ENV_FILE"
    echo "[FIX] Updated RESTREAM_DB_PATH=$FOUND_DB"
  else
    echo "[OK] RESTREAM_DB_PATH=$CURRENT_PATH"
  fi
fi

if grep -q '^DATABASE_URL=postgresql' "$ENV_FILE" || grep -q '^DATABASE_URL=postgres' "$ENV_FILE"; then
  echo
  echo "To switch back to SQLite automatically, run:"
  echo "  sed -i 's|^DATABASE_URL=.*|DATABASE_URL=|' $ENV_FILE"
fi

echo
echo "Current DB settings:"
grep -E '^(DATABASE_URL|RESTREAM_DB_PATH)=' "$ENV_FILE" || true

echo
echo "Restarting backend..."
if systemctl list-unit-files restream-backend.service >/dev/null 2>&1; then
  systemctl restart restream-backend
else
  kill $(pgrep -f "uvicorn main:app") 2>/dev/null || true
  cd "$REPO_DIR"
  nohup .venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000 > /var/log/restream-backend.log 2>&1 &
fi

sleep 2
echo
echo "Health:"
curl -sS http://127.0.0.1:8000/health || true
echo
echo "Login test (should be 401, not 500):"
curl -sS -o /tmp/restream-login.json -w "HTTP:%{http_code}\n" \
  -X POST http://127.0.0.1:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"healthcheck","password":"bad"}' || true
cat /tmp/restream-login.json 2>/dev/null || true
echo
