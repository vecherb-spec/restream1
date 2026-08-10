#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${RESTREAM_REPO_DIR:-/opt/restream}"
SERVICE_USER="${RESTREAM_SERVICE_USER:-restream}"

if [[ ! -d "$REPO_DIR/.git" ]]; then
  echo "Repository not found: $REPO_DIR"
  exit 1
fi

if [[ ! -x "$REPO_DIR/.venv/bin/uvicorn" && ! -x "$REPO_DIR/venv/bin/uvicorn" ]]; then
  echo "Create venv first:"
  echo "  cd $REPO_DIR && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --home "$REPO_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
  echo "Created system user: $SERVICE_USER"
fi

mkdir -p "$REPO_DIR/logs" "$REPO_DIR/state" "$REPO_DIR/backups"
chown -R "$SERVICE_USER:$SERVICE_USER" "$REPO_DIR/logs" "$REPO_DIR/state" "$REPO_DIR/backups"
# Allow reading app code/venv as the service user without making the whole tree world-writable.
chown -R "$SERVICE_USER:$SERVICE_USER" "$REPO_DIR/.venv" "$REPO_DIR/frontend" 2>/dev/null || true

# Ensure SRS webhook secret is rendered for host installs.
if [[ -x "$REPO_DIR/deploy/scripts/render_srs_conf.sh" ]]; then
  RESTREAM_SRS_BACKEND_HOOK="${RESTREAM_SRS_BACKEND_HOOK:-127.0.0.1:8000}" \
    "$REPO_DIR/deploy/scripts/render_srs_conf.sh"
fi

cp "$REPO_DIR/deploy/systemd/restream-backend.service" /etc/systemd/system/
cp "$REPO_DIR/deploy/systemd/restream-frontend.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable restream-backend restream-frontend
systemctl restart restream-backend restream-frontend
systemctl --no-pager --full status restream-backend restream-frontend

echo
curl -fsS http://127.0.0.1:8000/health
echo
echo "Systemd services installed as user=$SERVICE_USER"
