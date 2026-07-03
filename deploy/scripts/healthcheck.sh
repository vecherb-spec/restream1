#!/usr/bin/env bash
set -euo pipefail

API_URL="${RESTREAM_API_URL:-http://127.0.0.1:8000/health}"
FRONTEND_URL="${RESTREAM_FRONTEND_URL:-http://127.0.0.1:3000}"
SRS_RTMP_HOST="${RESTREAM_SRS_RTMP_HOST:-127.0.0.1}"
SRS_RTMP_PORT="${RESTREAM_SRS_RTMP_PORT:-1935}"
SRS_HLS_PORT="${RESTREAM_SRS_HLS_PORT:-8080}"

check_http() {
  local name="$1"
  local url="$2"
  if curl -fsS --max-time 5 "$url" >/dev/null; then
    echo "[OK] $name"
    return 0
  fi
  echo "[FAIL] $name ($url)"
  return 1
}

check_password_reset_api() {
  local base_url="${1%/}"
  local response
  local status
  status="$(curl -sS --max-time 5 -o /tmp/restream-forgot.json -w "%{http_code}" \
    -X POST "$base_url/api/auth/forgot-password" \
    -H "Content-Type: application/json" \
    -d '{"identifier":"healthcheck"}')"
  if [[ "$status" == "200" ]] && grep -q '"code":0' /tmp/restream-forgot.json; then
    echo "[OK] Password reset API"
    return 0
  fi
  echo "[FAIL] Password reset API ($base_url/api/auth/forgot-password -> HTTP $status)"
  cat /tmp/restream-forgot.json 2>/dev/null || true
  return 1
}

check_tcp() {
  local name="$1"
  local host="$2"
  local port="$3"
  if timeout 3 bash -c "echo >/dev/tcp/$host/$port" 2>/dev/null; then
    echo "[OK] $name"
    return 0
  fi
  echo "[FAIL] $name ($host:$port)"
  return 1
}

failed=0

API_BASE_URL="${RESTREAM_API_BASE_URL:-${API_URL%/health}}"
check_http "FastAPI health" "$API_URL" || failed=1
check_password_reset_api "$API_BASE_URL" || failed=1
check_http "Next.js frontend" "$FRONTEND_URL" || failed=1
check_tcp "SRS RTMP" "$SRS_RTMP_HOST" "$SRS_RTMP_PORT" || failed=1
check_tcp "SRS HLS" "$SRS_RTMP_HOST" "$SRS_HLS_PORT" || failed=1

if command -v systemctl >/dev/null 2>&1; then
  for service in restream-backend restream-frontend srs nginx; do
    if systemctl list-unit-files "$service.service" >/dev/null 2>&1; then
      if systemctl is-active --quiet "$service"; then
        echo "[OK] systemd $service"
      else
        echo "[FAIL] systemd $service"
        failed=1
      fi
    fi
  done
fi

exit "$failed"
