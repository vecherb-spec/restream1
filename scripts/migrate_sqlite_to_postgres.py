"""Copy Restream users/settings from SQLite to PostgreSQL.

Usage:
    python scripts/migrate_sqlite_to_postgres.py \
        --sqlite /opt/restream/restream.db \
        --postgres postgresql://restream:YOUR_STRONG_PASSWORD@127.0.0.1:5432/restream

The script intentionally does not migrate auth_sessions; users will sign in
again and receive new web sessions.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import Any


USER_FIELDS = [
    "id",
    "username",
    "password",
    "email",
    "role",
    "plan",
    "max_destinations",
    "stream_title",
    "stream_key",
    "is_active",
    "yt_active",
    "yt_key",
    "vk_active",
    "vk_url",
    "vk_key",
    "rt_active",
    "rt_url",
    "rt_key",
    "tg_active",
    "tg_url",
    "tg_key",
    "custom_active",
    "custom_url",
    "custom_key",
    "created_at",
]


def normalize_postgres_url(url: str) -> str:
    """Normalize legacy postgres:// URLs for psycopg."""

    if url.startswith("postgres://"):
        return "postgresql://" + url.removeprefix("postgres://")
    return url


def read_sqlite_users(sqlite_path: Path) -> list[dict[str, Any]]:
    """Read all user rows from SQLite."""

    connection = sqlite3.connect(sqlite_path)
    connection.row_factory = sqlite3.Row
    try:
        available = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(users)").fetchall()
        }
        fields = [field for field in USER_FIELDS if field in available]
        if not fields:
            raise RuntimeError("SQLite users table has no migratable columns")
        rows = connection.execute(
            f"SELECT {', '.join(fields)} FROM users ORDER BY id"
        ).fetchall()
        users: list[dict[str, Any]] = []
        for row in rows:
            payload = {field: None for field in USER_FIELDS}
            payload.update(dict(row))
            if not payload.get("stream_title"):
                payload["stream_title"] = "Live"
            users.append(payload)
        return users
    finally:
        connection.close()


def migrate_users(users: list[dict[str, Any]], postgres_url: str) -> None:
    """Upsert users into PostgreSQL."""

    import psycopg

    placeholders = ", ".join(["%s"] * len(USER_FIELDS))
    columns = ", ".join(USER_FIELDS)
    updates = ", ".join(
        f"{field} = EXCLUDED.{field}"
        for field in USER_FIELDS
        if field not in {"id", "username"}
    )

    sql = f"""
        INSERT INTO users ({columns})
        VALUES ({placeholders})
        ON CONFLICT (username) DO UPDATE SET {updates}
    """

    with psycopg.connect(normalize_postgres_url(postgres_url)) as connection:
        with connection.cursor() as cursor:
            for user in users:
                cursor.execute(sql, [user[field] for field in USER_FIELDS])

            max_id = max((int(user["id"]) for user in users), default=0)
            if max_id:
                cursor.execute(
                    "SELECT setval(pg_get_serial_sequence('users', 'id'), %s, true)",
                    (max_id,),
                )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite", required=True, type=Path)
    parser.add_argument("--postgres", required=True)
    args = parser.parse_args()

    users = read_sqlite_users(args.sqlite)
    migrate_users(users, args.postgres)
    print(f"Migrated {len(users)} users from {args.sqlite} to PostgreSQL.")


if __name__ == "__main__":
    main()
