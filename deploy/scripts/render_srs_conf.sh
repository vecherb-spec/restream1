#!/usr/bin/env bash
set -euo pipefail

# Render deploy/srs/srs.conf from template + env (Compose or host deploy).
# Fails if RESTREAM_SRS_WEBHOOK_SECRET is missing or insecure.

REPO_DIR="${RESTREAM_REPO_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
TEMPLATE="${RESTREAM_SRS_TEMPLATE:-$REPO_DIR/deploy/srs/srs.conf.template}"
OUTPUT="${RESTREAM_SRS_CONF:-$REPO_DIR/deploy/srs/srs.conf}"
ENV_FILE="${RESTREAM_ENV_FILE:-$REPO_DIR/.env}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
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

: "${RESTREAM_SRS_WEBHOOK_SECRET:?RESTREAM_SRS_WEBHOOK_SECRET is required}"
: "${RESTREAM_SRS_BACKEND_HOOK:?RESTREAM_SRS_BACKEND_HOOK is required (Docker: backend:8000, host: 127.0.0.1:8000)}"

SECRET="$RESTREAM_SRS_WEBHOOK_SECRET"
BACKEND_HOOK="$RESTREAM_SRS_BACKEND_HOOK"

if [[ -z "$SECRET" ]]; then
  echo "RESTREAM_SRS_WEBHOOK_SECRET is empty" >&2
  exit 1
fi

lower="$(printf '%s' "$SECRET" | tr '[:upper:]' '[:lower:]')"
if [[ "$lower" == *"change_me"* || "$lower" == *"changeme"* || "$lower" == *"__unrendered"* || "$lower" == *"replace_with"* ]]; then
  echo "RESTREAM_SRS_WEBHOOK_SECRET looks like an insecure default/placeholder" >&2
  exit 1
fi
if [[ "${#SECRET}" -lt 16 ]]; then
  echo "RESTREAM_SRS_WEBHOOK_SECRET must be at least 16 characters" >&2
  exit 1
fi

if [[ ! -f "$TEMPLATE" ]]; then
  echo "SRS template not found: $TEMPLATE" >&2
  exit 1
fi

tmp_output="$(mktemp)"
sed \
  -e "s|__SRS_WEBHOOK_SECRET__|${SECRET}|g" \
  -e "s|__SRS_BACKEND_HOOK__|${BACKEND_HOOK}|g" \
  "$TEMPLATE" > "$tmp_output"

if ! grep -q "token=${SECRET}" "$tmp_output"; then
  echo "Rendered SRS config does not contain the expected webhook secret token" >&2
  rm -f "$tmp_output"
  exit 1
fi
if grep -qiE 'change_me|changeme' "$tmp_output"; then
  echo "Rendered SRS config still contains insecure placeholder values" >&2
  rm -f "$tmp_output"
  exit 1
fi

mv "$tmp_output" "$OUTPUT"
echo "Rendered $OUTPUT (backend hook: $BACKEND_HOOK; secret verified, value not printed)"
