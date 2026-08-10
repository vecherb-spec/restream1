"""SQLite storage layer for the Restream MVP.

The module intentionally uses only the Python standard library so both the
FastAPI backend and the Streamlit UI can share one small, predictable data
access layer. Boolean values are stored as INTEGER (0/1), which is SQLite's
native convention.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import shutil
import sqlite3
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(dotenv_path: str | Path | None = None, **_kwargs: object) -> bool:
        env_path = Path(dotenv_path or ".env")
        if not env_path.is_file():
            return False
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
        return True

load_dotenv(Path(__file__).resolve().parent / ".env")

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
DATABASE_BACKEND = "postgres" if DATABASE_URL.startswith(("postgresql://", "postgres://")) else "sqlite"
DATABASE_PATH = Path(os.getenv("RESTREAM_DB_PATH", "restream.db"))
BACKUP_DIR = Path(os.getenv("RESTREAM_BACKUP_DIR", "backups"))

DEFAULT_ADMIN_USERNAME = os.getenv("RESTREAM_ADMIN_USERNAME", "admin").strip() or "admin"
DEFAULT_ADMIN_PASSWORD = os.getenv("RESTREAM_ADMIN_PASSWORD", "").strip()
YOUTUBE_RTMP_URL = "rtmp://a.rtmp.youtube.com/live2"
AUTH_SESSION_DAYS = int(os.getenv("RESTREAM_AUTH_SESSION_DAYS", "7"))
DEFAULT_CLIENT_PLAN = os.getenv("RESTREAM_DEFAULT_CLIENT_PLAN", "free")
DEFAULT_MAX_DESTINATIONS = int(os.getenv("RESTREAM_DEFAULT_MAX_DESTINATIONS", "1"))
ADMIN_MAX_DESTINATIONS = int(os.getenv("RESTREAM_ADMIN_MAX_DESTINATIONS", "99"))

PLAN_DESTINATION_LIMITS = {
    "free": DEFAULT_MAX_DESTINATIONS,
    "basic": 3,
    "pro": 5,
    "admin": ADMIN_MAX_DESTINATIONS,
}

DESTINATION_SPEC_CONFIGS = (
    {
        "id": "yt",
        "title": "YouTube",
        "active_field": "yt_active",
        "url_field": None,
        "key_field": "yt_key",
        "base_url": YOUTUBE_RTMP_URL,
    },
    {
        "id": "vk",
        "title": "VK",
        "active_field": "vk_active",
        "url_field": "vk_url",
        "key_field": "vk_key",
        "base_url": None,
    },
    {
        "id": "rt",
        "title": "Rutube",
        "active_field": "rt_active",
        "url_field": "rt_url",
        "key_field": "rt_key",
        "base_url": None,
    },
    {
        "id": "tg",
        "title": "Telegram",
        "active_field": "tg_active",
        "url_field": "tg_url",
        "key_field": "tg_key",
        "base_url": None,
    },
    {
        "id": "custom",
        "title": "Custom",
        "active_field": "custom_active",
        "url_field": "custom_url",
        "key_field": "custom_key",
        "base_url": None,
    },
)

logger = logging.getLogger("restream.database")


def normalize_database_url(url: str) -> str:
    """Normalize URL schemes accepted by psycopg."""

    if url.startswith("postgres://"):
        return "postgresql://" + url.removeprefix("postgres://")
    return url


class PostgresCursor:
    """Expose sqlite-style cursor helpers over psycopg results."""

    def __init__(self, cursor: Any) -> None:
        self._cursor = cursor

    @property
    def rowcount(self) -> int:
        return int(self._cursor.rowcount)

    def fetchone(self) -> Any:
        return self._cursor.fetchone()

    def fetchall(self) -> list[Any]:
        return self._cursor.fetchall()


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

    def execute(self, query: str, parameters: tuple[Any, ...] | list[Any] = ()) -> PostgresCursor:
        cursor = self._connection.execute(query.replace("?", "%s"), parameters)
        return PostgresCursor(cursor)

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


def is_password_hashed(stored_password: str) -> bool:
    """Return True when the stored password uses the current PBKDF2 format."""

    parts = (stored_password or "").split("$")
    return len(parts) == 3 and parts[0] == "pbkdf2_sha256"


ALLOW_PLAINTEXT_PASSWORDS = os.getenv("RESTREAM_ALLOW_PLAINTEXT_PASSWORDS", "false").lower() == "true"


def verify_password(password: str, stored_password: str) -> bool:
    """Verify PBKDF2 hashes; plaintext fallback is opt-in only."""

    if not stored_password:
        return False

    if is_password_hashed(stored_password):
        _, salt, expected_digest = stored_password.split("$", 2)
        actual_digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            120_000,
        ).hex()
        return secrets.compare_digest(actual_digest, expected_digest)

    if not ALLOW_PLAINTEXT_PASSWORDS:
        return False

    # Legacy plaintext rows (only when explicitly enabled).
    return secrets.compare_digest(password, stored_password)


def delete_auth_sessions_for_user(user_id: int) -> None:
    """Invalidate all web sessions for a user."""

    with get_connection() as connection:
        connection.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))


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


def check_database() -> tuple[bool, str]:
    """Verify that the configured database is reachable."""

    try:
        with get_connection() as connection:
            connection.execute("SELECT 1").fetchone()
        return True, DATABASE_BACKEND
    except Exception as exc:
        logger.exception("Database health check failed")
        return False, str(exc)


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
                stream_title TEXT NOT NULL DEFAULT 'Название трансляции',
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
        ensure_column(connection, "users", "stream_title", "TEXT NOT NULL DEFAULT 'Название трансляции'")
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
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                id {user_id_definition},
                user_id INTEGER NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                expires_at TEXT NOT NULL,
                used_at TEXT,
                created_at TEXT NOT NULL DEFAULT {timestamp_default},
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        connection.execute(
            "DELETE FROM password_reset_tokens WHERE expires_at <= ? OR used_at IS NOT NULL",
            (utc_now_iso(),),
        )

        admin = connection.execute(
            "SELECT id FROM users WHERE username = ?",
            (DEFAULT_ADMIN_USERNAME,),
        ).fetchone()
        if admin is None:
            if len(DEFAULT_ADMIN_PASSWORD) < 8:
                logger.warning(
                    "Admin user is missing. Set RESTREAM_ADMIN_PASSWORD in .env "
                    "(at least 8 characters) and restart backend to create it."
                )
            else:
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
                        os.getenv("RESTREAM_ADMIN_EMAIL", "admin@restream.medialive.ru").strip(),
                        "admin",
                        "admin",
                        ADMIN_MAX_DESTINATIONS,
                        generate_stream_key(DEFAULT_ADMIN_USERNAME),
                    ),
                )
                logger.info("Created admin user %s from RESTREAM_ADMIN_PASSWORD", DEFAULT_ADMIN_USERNAME)
        connection.execute(
            """
            UPDATE users
            SET plan = 'admin', max_destinations = ?
            WHERE username = ?
            """,
            (ADMIN_MAX_DESTINATIONS, DEFAULT_ADMIN_USERNAME),
        )
        for plan_name, limit in PLAN_DESTINATION_LIMITS.items():
            connection.execute(
                """
                UPDATE users
                SET max_destinations = ?
                WHERE lower(plan) = ?
                  AND max_destinations < ?
                """,
                (limit, plan_name, limit),
            )


def create_user(username: str, password: str, email: str) -> tuple[bool, str, dict[str, Any] | None]:
    """Register a client user and return (success, message, user)."""

    username = username.strip()
    email = email.strip()
    if not username or not password:
        return False, "Логин и пароль обязательны.", None
    if len(password) < 8:
        return False, "Пароль должен быть не короче 8 символов.", None

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
    if not user or not user["is_active"] or not verify_password(password, user["password"]):
        return None

    if not is_password_hashed(str(user.get("password") or "")):
        with get_connection() as connection:
            connection.execute(
                "UPDATE users SET password = ? WHERE id = ?",
                (hash_password(password), int(user["id"])),
            )
        fresh = get_user_by_id(int(user["id"]))
        return fresh or user
    return user


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


def create_password_reset_token(identifier: str) -> tuple[dict[str, Any] | None, str | None]:
    """Create a password reset token for a username or email when the user exists."""

    identifier = identifier.strip()
    if not identifier:
        return None, None

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT * FROM users
            WHERE is_active = 1
              AND (lower(username) = lower(?) OR lower(email) = lower(?))
            """,
            (identifier, identifier),
        ).fetchone()
        user = row_to_dict(row)
        if user is None:
            return None, None

        token = secrets.token_urlsafe(32)
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(timespec="seconds")
        connection.execute(
            "DELETE FROM password_reset_tokens WHERE user_id = ?",
            (int(user["id"]),),
        )
        connection.execute(
            """
            INSERT INTO password_reset_tokens (user_id, token_hash, expires_at)
            VALUES (?, ?, ?)
            """,
            (int(user["id"]), hash_session_token(token), expires_at),
        )
        return user, token


def reset_password_with_token(token: str, new_password: str) -> tuple[bool, str]:
    """Consume a password reset token and update the user's password."""

    if len(new_password) < 8:
        return False, "Новый пароль должен быть не короче 8 символов."
    if not token:
        return False, "Токен восстановления обязателен."

    now = utc_now_iso()
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT password_reset_tokens.id AS token_id, users.id AS user_id
            FROM password_reset_tokens
            JOIN users ON users.id = password_reset_tokens.user_id
            WHERE password_reset_tokens.token_hash = ?
              AND password_reset_tokens.expires_at > ?
              AND password_reset_tokens.used_at IS NULL
              AND users.is_active = 1
            """,
            (hash_session_token(token), now),
        ).fetchone()
        if row is None:
            return False, "Ссылка восстановления недействительна или истекла."

        token_id = row["token_id"]
        user_id = row["user_id"]
        connection.execute(
            "UPDATE users SET password = ? WHERE id = ?",
            (hash_password(new_password), user_id),
        )
        connection.execute(
            "UPDATE password_reset_tokens SET used_at = ? WHERE id = ?",
            (now, token_id),
        )
        connection.execute(
            "DELETE FROM auth_sessions WHERE user_id = ?",
            (user_id,),
        )

    return True, "Пароль обновлен. Теперь можно войти с новым паролем."


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
                id, username, email, role, plan, max_destinations, stream_title, stream_key, is_active,
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


def update_stream_title(user_id: int, stream_title: str) -> tuple[bool, str]:
    """Update the user's broadcast title."""

    title = stream_title.strip()
    if not title:
        return False, "Название трансляции не может быть пустым."
    if len(title) > 120:
        return False, "Название трансляции должно быть не длиннее 120 символов."

    with get_connection() as connection:
        cursor = connection.execute(
            "UPDATE users SET stream_title = ? WHERE id = ?",
            (title, user_id),
        )
    if cursor.rowcount == 0:
        return False, "Пользователь не найден."
    return True, "Название трансляции обновлено."


def update_user_password(user_id: int, new_password: str) -> tuple[bool, str]:
    """Set a new password hash for a user and revoke existing sessions."""

    if len(new_password) < 8:
        return False, "Пароль должен быть не короче 8 символов."

    with get_connection() as connection:
        cursor = connection.execute(
            "UPDATE users SET password = ? WHERE id = ?",
            (hash_password(new_password), user_id),
        )
        if cursor.rowcount == 0:
            return False, "Пользователь не найден."
    delete_auth_sessions_for_user(user_id)
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
    delete_auth_sessions_for_user(user_id)
    return True, "Пароль обновлен. Войдите снова с новым паролем."


def is_unique_violation(exc: Exception) -> bool:
    """Return True for SQLite/Postgres unique constraint failures."""

    if isinstance(exc, sqlite3.IntegrityError):
        return True
    # psycopg2 / psycopg3 unique violations
    if exc.__class__.__name__ in {"UniqueViolation", "IntegrityError"}:
        return True
    text = str(exc).lower()
    return "unique" in text or "duplicate key" in text or "already exists" in text


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
            except Exception as exc:
                if is_unique_violation(exc):
                    continue
                raise

    return False, "Не удалось сгенерировать уникальный stream key.", None


def get_enabled_platform_names(user: dict[str, Any]) -> list[str]:
    """Return human-readable names of enabled destination platforms."""

    return [spec["title"] for spec in get_enabled_destination_specs(user)]


def get_plan_destination_limit(user: dict[str, Any]) -> int:
    """Return the default destination limit for the user's plan name."""

    plan = str(user.get("plan") or DEFAULT_CLIENT_PLAN).strip().lower()
    return PLAN_DESTINATION_LIMITS.get(plan, DEFAULT_MAX_DESTINATIONS)


def get_custom_destination_limit(user: dict[str, Any]) -> int:
    """Return admin/manual override from users.max_destinations (0 = unset)."""

    try:
        return max(0, int(user.get("max_destinations") or 0))
    except (TypeError, ValueError):
        return 0


def get_user_destination_limit(user: dict[str, Any]) -> int:
    """Return effective destination limit: max(plan_limit, custom_limit).

    - plan_limit: default for free/basic/pro/admin
    - custom_limit: users.max_destinations set by admin
    - effective_limit: allows admin to raise above the plan without renaming the plan
    """

    plan_limit = get_plan_destination_limit(user)
    custom_limit = get_custom_destination_limit(user)
    if custom_limit > plan_limit:
        return custom_limit
    return plan_limit


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


def is_allowed_rtmp_base_url(base_url: str) -> bool:
    """Allow only RTMP/RTMPS destination bases for FFmpeg outputs."""

    lowered = (base_url or "").strip().lower()
    return lowered.startswith("rtmp://") or lowered.startswith("rtmps://")


def build_rtmp_target(base_url: str, stream_key: str) -> str:
    """Join an RTMP base URL and platform stream key into one FFmpeg target."""

    base_url = (base_url or "").strip()
    stream_key = (stream_key or "").strip()
    if not base_url or not stream_key:
        return ""
    if not is_allowed_rtmp_base_url(base_url):
        return ""
    if any(token in stream_key for token in ("://", " ", "\n", "\r", "\t")):
        return ""
    return f"{base_url.rstrip('/')}/{stream_key.lstrip('/')}"


def validate_destination_urls(user_or_settings: dict[str, Any]) -> tuple[bool, str]:
    """Reject enabled destinations that are not RTMP/RTMPS URLs."""

    checks = (
        ("VK", "vk_active", "vk_url"),
        ("Rutube", "rt_active", "rt_url"),
        ("Telegram", "tg_active", "tg_url"),
        ("Custom", "custom_active", "custom_url"),
    )
    for label, active_field, url_field in checks:
        if not user_or_settings.get(active_field):
            continue
        base_url = str(user_or_settings.get(url_field) or "").strip()
        if not base_url:
            continue
        if not is_allowed_rtmp_base_url(base_url):
            return False, f"{label}: URL должен начинаться с rtmp:// или rtmps://"
    return True, ""


def get_enabled_destinations(user: dict[str, Any]) -> list[str]:
    """Build all enabled RTMP targets for the user's restream settings."""

    return [spec["url"] for spec in get_enabled_destination_specs(user)]


def get_enabled_destination_specs(user: dict[str, Any]) -> list[dict[str, str]]:
    """Build enabled destination specs with stable platform ids and RTMP targets."""

    destinations: list[dict[str, str]] = []
    for config in DESTINATION_SPEC_CONFIGS:
        if not user.get(config["active_field"]):
            continue
        stream_key = str(user.get(config["key_field"]) or "").strip()
        if not stream_key:
            continue
        base_url = config["base_url"]
        if base_url is None:
            url_field = config["url_field"]
            base_url = str(user.get(url_field) or "").strip() if url_field else ""
        destination_url = build_rtmp_target(str(base_url or ""), stream_key)
        if not destination_url:
            continue
        destinations.append(
            {
                "id": str(config["id"]),
                "title": str(config["title"]),
                "url": destination_url,
            }
        )
    return destinations


try:
    init_db()
except Exception:
    logger.exception("Database initialization failed during import")
