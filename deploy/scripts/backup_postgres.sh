#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR="${RESTREAM_BACKUP_DIR:-/opt/restream/backups}"
DATABASE_URL="${DATABASE_URL:-}"
RETENTION_DAYS="${RESTREAM_BACKUP_RETENTION_DAYS:-14}"

if [[ -z "$DATABASE_URL" ]]; then
  echo "DATABASE_URL is not set. This script is for PostgreSQL backups only."
  exit 1
fi

mkdir -p "$BACKUP_DIR"

timestamp="$(date -u +%Y%m%d_%H%M%S)"
backup_file="${BACKUP_DIR}/restream_${timestamp}.sql"

pg_dump "$DATABASE_URL" > "$backup_file"
gzip -f "$backup_file"

echo "Backup created: ${backup_file}.gz"

find "$BACKUP_DIR" -type f \( -name 'restream_*.sql.gz' -o -name 'restream_*.sql' \) -mtime +"$RETENTION_DAYS" -delete
