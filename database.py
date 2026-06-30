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
import sqlite3
import uuid
from pathlib import Path
from typing import Any


DATABASE_PATH = Path(os.getenv("RESTREAM_DB_PATH", "restream.db"))

DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "admin_password_2026"
YOUTUBE_RTMP_URL = "rtmp://a.rtmp.youtube.com/live2"


def get_connection() -> sqlite3.Connection:
    """Create a SQLite connection configured for dictionary-like row access."""

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


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    """Convert sqlite3.Row objects to regular dictionaries."""

    return dict(row) if row is not None else None


def init_db() -> None:
    """Create the schema and seed the default administrator account."""

    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password TEXT NOT NULL,
                email TEXT,
                role TEXT NOT NULL DEFAULT 'client',
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
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        admin = connection.execute(
            "SELECT id FROM users WHERE username = ?",
            (DEFAULT_ADMIN_USERNAME,),
        ).fetchone()
        if admin is None:
            connection.execute(
                """
                INSERT INTO users (
                    username, password, email, role, stream_key, is_active
                )
                VALUES (?, ?, ?, ?, ?, 1)
                """,
                (
                    DEFAULT_ADMIN_USERNAME,
                    hash_password(DEFAULT_ADMIN_PASSWORD),
                    "admin@restream.medialive.ru",
                    "admin",
                    generate_stream_key(DEFAULT_ADMIN_USERNAME),
                ),
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
                cursor = connection.execute(
                    """
                    INSERT INTO users (username, password, email, role, stream_key)
                    VALUES (?, ?, ?, 'client', ?)
                    """,
                    (username, hash_password(password), email, stream_key),
                )
                row = connection.execute(
                    "SELECT * FROM users WHERE id = ?",
                    (cursor.lastrowid,),
                ).fetchone()
                user = row_to_dict(row)
                return True, "Пользователь зарегистрирован.", user
            except sqlite3.IntegrityError as exc:
                if "stream_key" in str(exc).lower():
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
                id, username, email, role, stream_key, is_active,
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
