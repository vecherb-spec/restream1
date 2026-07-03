#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${RESTREAM_REPO_DIR:-/opt/restream}"

if [[ ! -d "$REPO_DIR/.git" ]]; then
  echo "Repository not found: $REPO_DIR"
  exit 1
fi

if [[ ! -x "$REPO_DIR/.venv/bin/uvicorn" && ! -x "$REPO_DIR/venv/bin/uvicorn" ]]; then
  echo "Create venv first:"
  echo "  cd $REPO_DIR && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi

cp "$REPO_DIR/deploy/systemd/restream-backend.service" /etc/systemd/system/
cp "$REPO_DIR/deploy/systemd/restream-frontend.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable restream-backend restream-frontend
systemctl restart restream-backend restream-frontend
systemctl --no-pager --full status restream-backend restream-frontend

echo
echo "Check password reset API:"
curl -sS -X POST http://127.0.0.1:8000/api/auth/forgot-password \
  -H "Content-Type: application/json" \
  -d '{"identifier":"healthcheck"}'
