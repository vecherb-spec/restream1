#!/usr/bin/env bash
set -euo pipefail

# Render deploy/srs/srs.conf from template + env (Compose or host deploy).

REPO_DIR="${RESTREAM_REPO_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
TEMPLATE="${RESTREAM_SRS_TEMPLATE:-$REPO_DIR/deploy/srs/srs.conf.template}"
OUTPUT="${RESTREAM_SRS_CONF:-$REPO_DIR/deploy/srs/srs.conf}"
ENV_FILE="${RESTREAM_ENV_FILE:-$REPO_DIR/.env}"

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  set -a
  # Prefer simple KEY=VALUE lines; ignore comments.
  while IFS= read -r line; do
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" != *=* ]] && continue
    key="${line%%=*}"
    value="${line#*=}"
    key="$(echo "$key" | xargs)"
    value="$(echo "$value" | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//")"
    export "$key=$value"
  done < "$ENV_FILE"
  set +a
fi

SECRET="${RESTREAM_SRS_WEBHOOK_SECRET:-change_me_srs_webhook_secret}"
BACKEND_HOOK="${RESTREAM_SRS_BACKEND_HOOK:-backend:8000}"

if [[ ! -f "$TEMPLATE" ]]; then
  echo "SRS template not found: $TEMPLATE" >&2
  exit 1
fi

sed \
  -e "s|__SRS_WEBHOOK_SECRET__|${SECRET}|g" \
  -e "s|__SRS_BACKEND_HOOK__|${BACKEND_HOOK}|g" \
  "$TEMPLATE" > "$OUTPUT"

echo "Rendered $OUTPUT (backend hook: $BACKEND_HOOK)"
