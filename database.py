"""SQLite storage layer for the Restream MVP.

The module intentionally uses only the Python standard library so both the
FastAPI backend and the Streamlit UI can share one small, predictable data
access layer. Boolean values are stored as INTEGER (0/1), which is SQLite's
native convention.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import shutil
import sqlite3
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
DATABASE_BACKEND = "postgres" if DATABASE_URL.startswith(("postgresql://", "postgres://")) else "sqlite"
DATABASE_PATH = Path(os.getenv("RESTREAM_DB_PATH", "restream.db"))
BACKUP_DIR = Path(os.getenv("RESTREAM_BACKUP_DIR", "backups"))

DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "admin_password_2026"
YOUTUBE_RTMP_URL = "rtmp://a.rtmp.youtube.com/live2"
AUTH_SESSION_DAYS = int(os.getenv("RESTREAM_AUTH_SESSION_DAYS", "7"))
DEFAULT_CLIENT_PLAN = os.getenv("RESTREAM_DEFAULT_CLIENT_PLAN", "free")
DEFAULT_MAX_DESTINATIONS = int(os.getenv("RESTREAM_DEFAULT_MAX_DESTINATIONS", "1"))
ADMIN_MAX_DESTINATIONS = int(os.getenv("RESTREAM_ADMIN_MAX_DESTINATIONS", "99"))


def normalize_database_url(url: str) -> str:
    """Normalize URL schemes accepted by psycopg."""

    if url.startswith("postgres://"):
        return "postgresql://" + url.removeprefix("postgres://")
    return url


class PostgresConnection:
    """Small compatibility wrapper around psycopg connections.

    The existing MVP storage layer uses sqlite3-style `?` placeholders. This
    wrapper lets the same functions talk to PostgreSQL during the migration
    phase without rewriting every query at once.
    """

    def __init__(self) -> None:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError(
                "PostgreSQL support requires psycopg[binary]. Install requirements.txt first."
            ) from exc

        self._connection = psycopg.connect(
            normalize_database_url(DATABASE_URL),
            row_factory=dict_row,
        )

    def execute(self, query: str, parameters: tuple[Any, ...] | list[Any] = ()) -> Any:
        return self._connection.execute(query.replace("?", "%s"), parameters)

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "PostgresConnection":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        self.close()


def get_connection() -> sqlite3.Connection | PostgresConnection:
    """Create a DB connection configured for dictionary-like row access."""

    if DATABASE_BACKEND == "postgres":
        return PostgresConnection()

    connection = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def hash_password(password: str) -> str:
    """Hash a password with PBKDF2 and return a portable encoded value."""

    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        120_000,
    ).hex()
    return f"pbkdf2_sha256${salt}${digest}"


def verify_password(password: str, stored_password: str) -> bool:
    """Verify PBKDF2 hashes while still accepting legacy plaintext values."""

    if not stored_password:
        return False

    parts = stored_password.split("$")
    if len(parts) == 3 and parts[0] == "pbkdf2_sha256":
        _, salt, expected_digest = parts
        actual_digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            120_000,
        ).hex()
        return secrets.compare_digest(actual_digest, expected_digest)

    # MVP-friendly fallback if older rows were created with plaintext passwords.
    return secrets.compare_digest(password, stored_password)


def generate_stream_key(username: str | None = None) -> str:
    """Generate a stream key suitable for OBS/SRS publishing."""

    safe_username = "".join(
        char.lower() if char.isalnum() else "_" for char in (username or "user")
    ).strip("_")
    safe_username = safe_username or "user"
    return f"live_{safe_username}_{uuid.uuid4().hex[:12]}"


def row_to_dict(row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any] | None:
    """Convert DB row objects to regular dictionaries."""

    return dict(row) if row is not None else None


def utc_now_iso() -> str:
    """Return current UTC time in a SQLite-friendly ISO format."""

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def column_exists(connection: sqlite3.Connection | PostgresConnection, table: str, column: str) -> bool:
    """Return whether a table column already exists."""

    if DATABASE_BACKEND == "postgres":
        row = connection.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = ?
              AND column_name = ?
            """,
            (table, column),
        ).fetchone()
        return row is not None

    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row["name"] == column for row in rows)


def ensure_column(
    connection: sqlite3.Connection | PostgresConnection,
    table: str,
    column: str,
    definition: str,
) -> None:
    """Add a column to an existing table when it is missing."""

    if not column_exists(connection, table, column):
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db() -> None:
    """Create the schema and seed the default administrator account."""

    with get_connection() as connection:
        user_id_definition = (
            "SERIAL PRIMARY KEY"
            if DATABASE_BACKEND == "postgres"
            else "INTEGER PRIMARY KEY AUTOINCREMENT"
        )
        timestamp_default = (
            "(CURRENT_TIMESTAMP::text)"
            if DATABASE_BACKEND == "postgres"
            else "CURRENT_TIMESTAMP"
        )
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS users (
                id {user_id_definition},
                username TEXT NOT NULL UNIQUE,
                password TEXT NOT NULL,
                email TEXT,
                role TEXT NOT NULL DEFAULT 'client',
                plan TEXT NOT NULL DEFAULT 'free',
                max_destinations INTEGER NOT NULL DEFAULT 1,
                stream_key TEXT NOT NULL UNIQUE,
                is_active INTEGER NOT NULL DEFAULT 1,
                yt_active INTEGER NOT NULL DEFAULT 0,
                yt_key TEXT DEFAULT '',
                vk_active INTEGER NOT NULL DEFAULT 0,
                vk_url TEXT DEFAULT '',
                vk_key TEXT DEFAULT '',
                rt_active INTEGER NOT NULL DEFAULT 0,
                rt_url TEXT DEFAULT '',
                rt_key TEXT DEFAULT '',
                tg_active INTEGER NOT NULL DEFAULT 0,
                tg_url TEXT DEFAULT '',
                tg_key TEXT DEFAULT '',
                custom_active INTEGER NOT NULL DEFAULT 0,
                custom_url TEXT DEFAULT '',
                custom_key TEXT DEFAULT '',
                created_at TEXT NOT NULL DEFAULT {timestamp_default}
            )
            """
        )
        ensure_column(connection, "users", "plan", "TEXT NOT NULL DEFAULT 'free'")
        ensure_column(connection, "users", "max_destinations", "INTEGER NOT NULL DEFAULT 1")
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS auth_sessions (
                id {user_id_definition},
                user_id INTEGER NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT {timestamp_default},
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        connection.execute(
            "DELETE FROM auth_sessions WHERE expires_at <= ?",
            (utc_now_iso(),),
        )

        admin = connection.execute(
            "SELECT id FROM users WHERE username = ?",
            (DEFAULT_ADMIN_USERNAME,),
        ).fetchone()
        if admin is None:
            connection.execute(
                """
                INSERT INTO users (
                    username, password, email, role, plan, max_destinations, stream_key, is_active
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    DEFAULT_ADMIN_USERNAME,
                    hash_password(DEFAULT_ADMIN_PASSWORD),
                    "admin@restream.medialive.ru",
                    "admin",
                    "admin",
                    ADMIN_MAX_DESTINATIONS,
                    generate_stream_key(DEFAULT_ADMIN_USERNAME),
                ),
            )
        connection.execute(
            """
            UPDATE users
            SET plan = 'admin', max_destinations = ?
            WHERE username = ?
            """,
            (ADMIN_MAX_DESTINATIONS, DEFAULT_ADMIN_USERNAME),
        )


def create_user(username: str, password: str, email: str) -> tuple[bool, str, dict[str, Any] | None]:
    """Register a client user and return (success, message, user)."""

    username = username.strip()
    email = email.strip()
    if not username or not password:
        return False, "Логин и пароль обязательны.", None

    with get_connection() as connection:
        for _ in range(5):
            stream_key = generate_stream_key(username)
            try:
                connection.execute(
                    """
                    INSERT INTO users (
                        username, password, email, role, plan, max_destinations, stream_key
                    )
                    VALUES (?, ?, ?, 'client', ?, ?, ?)
                    """,
                    (
                        username,
                        hash_password(password),
                        email,
                        DEFAULT_CLIENT_PLAN,
                        DEFAULT_MAX_DESTINATIONS,
                        stream_key,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM users WHERE username = ?",
                    (username,),
                ).fetchone()
                user = row_to_dict(row)
                return True, "Пользователь зарегистрирован.", user
            except Exception as exc:
                error_text = str(exc).lower()
                if "stream_key" in error_text:
                    continue
                return False, "Пользователь с таким логином уже существует.", None

    return False, "Не удалось сгенерировать уникальный ключ потока.", None


def authenticate_user(username: str, password: str) -> dict[str, Any] | None:
    """Return a user when credentials are valid and the account is active."""

    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM users WHERE username = ?",
            (username.strip(),),
        ).fetchone()

    user = row_to_dict(row)
    if user and user["is_active"] and verify_password(password, user["password"]):
        return user
    return None


def hash_session_token(token: str) -> str:
    """Hash a session token before storing or querying it."""

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_auth_session(user_id: int) -> str:
    """Create a persistent web session and return the raw token for the browser."""

    token = secrets.token_urlsafe(32)
    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=AUTH_SESSION_DAYS)
    ).isoformat(timespec="seconds")

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO auth_sessions (user_id, token_hash, expires_at)
            VALUES (?, ?, ?)
            """,
            (user_id, hash_session_token(token), expires_at),
        )
    return token


def get_user_by_session_token(token: str) -> dict[str, Any] | None:
    """Resolve a valid non-expired session token to an active user."""

    if not token:
        return None

    with get_connection() as connection:
        connection.execute(
            "DELETE FROM auth_sessions WHERE expires_at <= ?",
            (utc_now_iso(),),
        )
        row = connection.execute(
            """
            SELECT users.*
            FROM auth_sessions
            JOIN users ON users.id = auth_sessions.user_id
            WHERE auth_sessions.token_hash = ?
              AND auth_sessions.expires_at > ?
              AND users.is_active = 1
            """,
            (hash_session_token(token), utc_now_iso()),
        ).fetchone()

    return row_to_dict(row)


def delete_auth_session(token: str) -> None:
    """Delete a persistent web session."""

    if not token:
        return

    with get_connection() as connection:
        connection.execute(
            "DELETE FROM auth_sessions WHERE token_hash = ?",
            (hash_session_token(token),),
        )


def create_database_backup() -> dict[str, Any]:
    """Create a timestamped database backup."""

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    if DATABASE_BACKEND == "postgres":
        backup_path = BACKUP_DIR / f"restream_{timestamp}.sql"
        subprocess.run(
            ["pg_dump", normalize_database_url(DATABASE_URL), "-f", str(backup_path)],
            check=True,
            capture_output=True,
            text=True,
        )
    else:
        backup_path = BACKUP_DIR / f"restream_{timestamp}.db"
        shutil.copy2(DATABASE_PATH, backup_path)
    return {
        "path": str(backup_path),
        "filename": backup_path.name,
        "size_bytes": backup_path.stat().st_size,
        "created_at": utc_now_iso(),
    }


def list_database_backups(limit: int = 14) -> list[dict[str, Any]]:
    """List recent database backups."""

    if not BACKUP_DIR.exists():
        return []

    backups = sorted(
        (
            path
            for pattern in ("restream_*.db", "restream_*.sql")
            for path in BACKUP_DIR.glob(pattern)
            if path.is_file()
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return [
        {
            "path": str(path),
            "filename": path.name,
            "size_bytes": path.stat().st_size,
            "created_at": datetime.fromtimestamp(
                path.stat().st_mtime,
                tz=timezone.utc,
            ).isoformat(timespec="seconds"),
        }
        for path in backups[:limit]
    ]


def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    """Fetch a user by primary key."""

    with get_connection() as connection:
        row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return row_to_dict(row)


def get_user_by_stream_key(stream_key: str) -> dict[str, Any] | None:
    """Fetch a user by OBS/SRS stream key."""

    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM users WHERE stream_key = ?",
            (stream_key,),
        ).fetchone()
    return row_to_dict(row)


def get_active_user_by_stream_key(stream_key: str) -> dict[str, Any] | None:
    """Fetch an active user by stream key for SRS publish authorization."""

    user = get_user_by_stream_key(stream_key)
    if user and bool(user["is_active"]):
        return user
    return None


def list_users() -> list[dict[str, Any]]:
    """Return all users for the admin dashboard."""

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                id, username, email, role, plan, max_destinations, stream_key, is_active,
                yt_active, vk_active, rt_active, tg_active, custom_active, created_at
            FROM users
            ORDER BY id ASC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def set_user_active(user_id: int, is_active: bool) -> None:
    """Block or unblock a user account."""

    with get_connection() as connection:
        connection.execute(
            "UPDATE users SET is_active = ? WHERE id = ?",
            (1 if is_active else 0, user_id),
        )


def update_user_plan(user_id: int, plan: str, max_destinations: int) -> tuple[bool, str]:
    """Update user's commercial plan and destination limit."""

    plan = plan.strip().lower()
    if not plan:
        return False, "Название тарифа обязательно."
    if max_destinations < 0:
        return False, "Лимит площадок не может быть отрицательным."

    with get_connection() as connection:
        cursor = connection.execute(
            "UPDATE users SET plan = ?, max_destinations = ? WHERE id = ?",
            (plan, max_destinations, user_id),
        )
    if cursor.rowcount == 0:
        return False, "Пользователь не найден."
    return True, "Тариф обновлен."


def update_user_password(user_id: int, new_password: str) -> tuple[bool, str]:
    """Set a new password hash for a user."""

    if len(new_password) < 8:
        return False, "Пароль должен быть не короче 8 символов."

    with get_connection() as connection:
        cursor = connection.execute(
            "UPDATE users SET password = ? WHERE id = ?",
            (hash_password(new_password), user_id),
        )

    if cursor.rowcount == 0:
        return False, "Пользователь не найден."
    return True, "Пароль обновлен."


def change_user_password(
    user_id: int,
    current_password: str,
    new_password: str,
) -> tuple[bool, str]:
    """Change a user's own password after verifying the current password."""

    if len(new_password) < 8:
        return False, "Новый пароль должен быть не короче 8 символов."

    with get_connection() as connection:
        row = connection.execute(
            "SELECT password FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if row is None:
            return False, "Пользователь не найден."
        if not verify_password(current_password, row["password"]):
            return False, "Текущий пароль неверный."
        connection.execute(
            "UPDATE users SET password = ? WHERE id = ?",
            (hash_password(new_password), user_id),
        )
    return True, "Пароль обновлен."


def regenerate_user_stream_key(user_id: int) -> tuple[bool, str, str | None]:
    """Generate and store a new OBS/SRS stream key for a user."""

    with get_connection() as connection:
        row = connection.execute(
            "SELECT username FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if row is None:
            return False, "Пользователь не найден.", None

        for _ in range(5):
            stream_key = generate_stream_key(row["username"])
            try:
                connection.execute(
                    "UPDATE users SET stream_key = ? WHERE id = ?",
                    (stream_key, user_id),
                )
                return True, "Новый stream key сгенерирован.", stream_key
            except sqlite3.IntegrityError:
                continue

    return False, "Не удалось сгенерировать уникальный stream key.", None


def get_enabled_platform_names(user: dict[str, Any]) -> list[str]:
    """Return human-readable names of enabled destination platforms."""

    platforms: list[str] = []
    if user.get("yt_active") and user.get("yt_key"):
        platforms.append("YouTube")
    if user.get("vk_active") and user.get("vk_url") and user.get("vk_key"):
        platforms.append("VK")
    if user.get("rt_active") and user.get("rt_url") and user.get("rt_key"):
        platforms.append("Rutube")
    if user.get("tg_active") and user.get("tg_url") and user.get("tg_key"):
        platforms.append("Telegram")
    if user.get("custom_active") and user.get("custom_url") and user.get("custom_key"):
        platforms.append("Custom")
    return platforms


def get_user_destination_limit(user: dict[str, Any]) -> int:
    """Return max allowed active restream destinations for a user."""

    try:
        return max(0, int(user.get("max_destinations") or DEFAULT_MAX_DESTINATIONS))
    except (TypeError, ValueError):
        return DEFAULT_MAX_DESTINATIONS


def count_enabled_destinations(user_or_settings: dict[str, Any]) -> int:
    """Count enabled destinations with enough fields to build an RTMP target."""

    return len(get_enabled_destinations(user_or_settings))


def validate_destination_limit(user_or_settings: dict[str, Any]) -> tuple[bool, str]:
    """Validate that enabled destinations fit the user's plan limit."""

    active_destinations = count_enabled_destinations(user_or_settings)
    limit = get_user_destination_limit(user_or_settings)
    if active_destinations > limit:
        return (
            False,
            f"Текущий тариф разрешает {limit} активных площадок, выбрано {active_destinations}.",
        )
    return True, ""


def update_restream_settings(user_id: int, settings: dict[str, Any]) -> None:
    """Update destination settings for a client account."""

    allowed_fields = {
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
    }
    payload = {key: settings[key] for key in allowed_fields if key in settings}
    if not payload:
        return

    normalized_payload = {
        key: (1 if bool(value) else 0) if key.endswith("_active") else str(value).strip()
        for key, value in payload.items()
    }
    assignments = ", ".join(f"{field} = ?" for field in normalized_payload)
    values = list(normalized_payload.values()) + [user_id]

    with get_connection() as connection:
        connection.execute(
            f"UPDATE users SET {assignments} WHERE id = ?",
            values,
        )


def build_rtmp_target(base_url: str, stream_key: str) -> str:
    """Join an RTMP base URL and platform stream key into one FFmpeg target."""

    base_url = (base_url or "").strip()
    stream_key = (stream_key or "").strip()
    if not base_url or not stream_key:
        return ""
    return f"{base_url.rstrip('/')}/{stream_key.lstrip('/')}"


def get_enabled_destinations(user: dict[str, Any]) -> list[str]:
    """Build all enabled RTMP targets for the user's restream settings."""

    destinations: list[str] = []

    if user.get("yt_active") and user.get("yt_key"):
        destinations.append(build_rtmp_target(YOUTUBE_RTMP_URL, user["yt_key"]))
    if user.get("vk_active") and user.get("vk_url") and user.get("vk_key"):
        destinations.append(build_rtmp_target(user["vk_url"], user["vk_key"]))
    if user.get("rt_active") and user.get("rt_url") and user.get("rt_key"):
        destinations.append(build_rtmp_target(user["rt_url"], user["rt_key"]))
    if user.get("tg_active") and user.get("tg_url") and user.get("tg_key"):
        destinations.append(build_rtmp_target(user["tg_url"], user["tg_key"]))
    if user.get("custom_active") and user.get("custom_url") and user.get("custom_key"):
        destinations.append(build_rtmp_target(user["custom_url"], user["custom_key"]))

    return [destination for destination in destinations if destination]


init_db()
