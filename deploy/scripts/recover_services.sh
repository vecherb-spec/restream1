#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HEALTHCHECK="${SCRIPT_DIR}/healthcheck.sh"

if [[ ! -x "$HEALTHCHECK" ]]; then
  chmod +x "$HEALTHCHECK"
fi

if "$HEALTHCHECK"; then
  echo "All checks passed, recovery is not required."
  exit 0
fi

echo "Healthcheck failed, attempting service recovery..."

if command -v systemctl >/dev/null 2>&1; then
  for service in restream-backend restream-frontend srs nginx; do
    if systemctl list-unit-files "$service.service" >/dev/null 2>&1; then
      if ! systemctl is-active --quiet "$service"; then
        echo "Restarting $service..."
        systemctl restart "$service"
      fi
    fi
  done
fi

sleep 5

if "$HEALTHCHECK"; then
  echo "Recovery succeeded."
  exit 0
fi

echo "Recovery finished, but healthcheck is still failing."
exit 1
