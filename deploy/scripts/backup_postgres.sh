#!/usr/bin/env bash
set -euo pipefail

# Owner-only backups; never put DATABASE_URL password into pg_dump argv.
umask 077

BACKUP_DIR="${RESTREAM_BACKUP_DIR:-/opt/restream/backups}"
DATABASE_URL="${DATABASE_URL:-}"
RETENTION_DAYS="${RESTREAM_BACKUP_RETENTION_DAYS:-14}"

if [[ -z "$DATABASE_URL" ]]; then
  echo "DATABASE_URL is not set. This script is for PostgreSQL backups only."
  exit 1
fi

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

# Harden permissions on any existing backup artifacts.
find "$BACKUP_DIR" -maxdepth 1 -type f \( \
  -name 'restream_*.sql' -o -name 'restream_*.sql.gz' -o -name 'restream_*.dump' -o -name 'restream_*.gz' \
\) -exec chmod 600 {} +

timestamp="$(date -u +%Y%m%d_%H%M%S)"
backup_file="${BACKUP_DIR}/restream_${timestamp}.sql"
passfile="$(mktemp)"
chmod 600 "$passfile"

cleanup() {
  rm -f "$passfile"
}
trap cleanup EXIT

# Build PGPASSFILE + discrete pg_dump args without embedding the password in argv.
# DATABASE_URL is read from the environment (not argv) by this short-lived helper.
read -r PG_HOST PG_PORT PG_DB PG_USER < <(
  DATABASE_URL="$DATABASE_URL" python3 - "$passfile" <<'PY'
import os
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

url = os.environ.get("DATABASE_URL", "").strip()
passfile = Path(sys.argv[1])
parsed = urlparse(url if "://" in url else f"postgresql://{url}")
if not parsed.scheme.startswith("postgres"):
    raise SystemExit("DATABASE_URL must be a postgresql:// URL")

host = parsed.hostname or "localhost"
port = str(parsed.port or 5432)
database = unquote((parsed.path or "").lstrip("/"))
user = unquote(parsed.username or "")
password = unquote(parsed.password or "")
if not database or not user:
    raise SystemExit("DATABASE_URL must include username and database name")

escaped = password.replace("\\", "\\\\").replace(":", "\\:")
passfile.write_text(f"{host}:{port}:{database}:{user}:{escaped}\n", encoding="utf-8")
passfile.chmod(0o600)
print(host, port, database, user)
PY
)

export PGPASSFILE="$passfile"
unset PGPASSWORD || true

pg_dump \
  -h "$PG_HOST" \
  -p "$PG_PORT" \
  -U "$PG_USER" \
  -d "$PG_DB" \
  --no-password \
  -f "$backup_file"

chmod 600 "$backup_file"
gzip -f "$backup_file"
chmod 600 "${backup_file}.gz"

echo "Backup created: ${backup_file}.gz"

find "$BACKUP_DIR" -type f \( -name 'restream_*.sql.gz' -o -name 'restream_*.sql' \) -mtime +"$RETENTION_DAYS" -delete
