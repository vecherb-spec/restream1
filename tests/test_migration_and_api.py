"""Migration field coverage, auth API behavior, and optional Postgres tests."""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Unique DB file per process so repeated pytest runs do not collide.
_DB_FD, _DB_PATH = tempfile.mkstemp(prefix="restream-migration-api-", suffix=".db")
os.close(_DB_FD)
os.environ["RESTREAM_DB_PATH"] = _DB_PATH
os.environ["RESTREAM_ADMIN_PASSWORD"] = "test-admin-password-123"
os.environ["RESTREAM_ALLOW_PLAINTEXT_PASSWORDS"] = "false"
os.environ["RESTREAM_ALLOW_INSECURE_DEFAULTS"] = "true"
os.environ["RESTREAM_SRS_WEBHOOK_SECRET"] = "unit-test-srs-secret-value"


def load_migrate_module():
    path = ROOT / "scripts" / "migrate_sqlite_to_postgres.py"
    spec = importlib.util.spec_from_file_location("migrate_sqlite_to_postgres", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MigrationFieldTests(unittest.TestCase):
    def test_user_fields_include_stream_title(self) -> None:
        migrate = load_migrate_module()
        self.assertIn("stream_title", migrate.USER_FIELDS)
        self.assertIn("stream_key", migrate.USER_FIELDS)
        self.assertIn("max_destinations", migrate.USER_FIELDS)

    def test_read_sqlite_users_preserves_stream_title(self) -> None:
        import sqlite3

        migrate = load_migrate_module()

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "src.db"
            connection = sqlite3.connect(db_path)
            connection.execute(
                """
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY,
                    username TEXT,
                    password TEXT,
                    email TEXT,
                    role TEXT,
                    plan TEXT,
                    max_destinations INTEGER,
                    stream_title TEXT,
                    stream_key TEXT,
                    is_active INTEGER,
                    yt_active INTEGER DEFAULT 0,
                    yt_key TEXT,
                    vk_active INTEGER DEFAULT 0,
                    vk_url TEXT,
                    vk_key TEXT,
                    rt_active INTEGER DEFAULT 0,
                    rt_url TEXT,
                    rt_key TEXT,
                    tg_active INTEGER DEFAULT 0,
                    tg_url TEXT,
                    tg_key TEXT,
                    custom_active INTEGER DEFAULT 0,
                    custom_url TEXT,
                    custom_key TEXT,
                    created_at TEXT
                )
                """
            )
            connection.execute(
                """
                INSERT INTO users (
                    id, username, password, email, role, plan, max_destinations,
                    stream_title, stream_key, is_active
                ) VALUES (1, 'alice', 'x', 'a@b.c', 'client', 'basic', 3,
                          'My Stream Title', 'live_alice_abcd1234ef01', 1)
                """
            )
            connection.commit()
            connection.close()

            users = migrate.read_sqlite_users(db_path)
            self.assertEqual(len(users), 1)
            self.assertEqual(users[0]["stream_title"], "My Stream Title")
            self.assertEqual(users[0]["username"], "alice")


class AuthApiTests(unittest.TestCase):
    def test_forgot_password_response_is_uniform(self) -> None:
        import main
        from fastapi.testclient import TestClient

        client = TestClient(main.app)
        with mock.patch("main.create_password_reset_token", return_value=(None, None)):
            missing = client.post("/api/auth/forgot-password", json={"identifier": "nobody"})
        with mock.patch(
            "main.create_password_reset_token",
            return_value=({"id": 1, "email": "a@b.c"}, "tok"),
        ):
            with mock.patch("main.send_password_reset_email", return_value={"delivery": "smtp"}):
                existing = client.post("/api/auth/forgot-password", json={"identifier": "alice"})

        self.assertEqual(missing.status_code, 200)
        self.assertEqual(existing.status_code, 200)
        self.assertEqual(missing.json(), existing.json())
        self.assertNotIn("delivery", missing.json())

    def test_webhook_rejects_bad_secret(self) -> None:
        import main
        from fastapi.testclient import TestClient

        client = TestClient(main.app)
        response = client.post(
            "/on_publish",
            json={"stream": "live_x"},
            headers={"X-Restream-Webhook-Secret": "wrong-secret"},
        )
        self.assertEqual(response.status_code, 403)

    def test_webhook_accepts_query_token(self) -> None:
        import main
        from fastapi.testclient import TestClient

        client = TestClient(main.app)
        with mock.patch("main.get_active_user_by_stream_key", return_value=None):
            response = client.post(
                "/on_publish?token=unit-test-srs-secret-value",
                json={"stream": "live_unknown_key"},
            )
        # Authorized by secret, rejected by business logic (unknown stream).
        self.assertEqual(response.status_code, 403)
        self.assertIn("not allowed", str(response.json().get("message", "")).lower())


class DatabaseSqliteIntegrationTests(unittest.TestCase):
    def test_user_login_stream_title_and_limits(self) -> None:
        import database
        from database import (
            authenticate_user,
            create_user,
            get_user_by_id,
            get_user_destination_limit,
            init_db,
            update_restream_settings,
            update_stream_title,
        )

        # Keep this test on an isolated SQLite file even if another module imported database first.
        database.DATABASE_URL = ""
        database.DATABASE_BACKEND = "sqlite"
        database.DATABASE_PATH = Path(_DB_PATH)
        if Path(_DB_PATH).exists():
            Path(_DB_PATH).unlink()

        init_db()
        username = f"limit_user_{uuid.uuid4().hex[:12]}"
        ok, message, user = create_user(username, "password123", f"{username}@example.com")
        self.assertTrue(ok, message)
        assert user is not None
        self.assertEqual(get_user_destination_limit(user), 1)

        update_restream_settings(int(user["id"]), {"yt_active": 1, "yt_key": "k"})
        success, title_message = update_stream_title(int(user["id"]), "Night Show")
        self.assertTrue(success, title_message)
        auth = authenticate_user(username, "password123")
        assert auth is not None
        self.assertEqual(auth["stream_title"], "Night Show")
        fresh = get_user_by_id(int(user["id"]))
        assert fresh is not None
        self.assertEqual(fresh["stream_title"], "Night Show")


# PostgreSQL integration tests live in tests/test_postgres_integration.py
# and are launched with ./deploy/scripts/run_postgres_tests.sh


if __name__ == "__main__":
    unittest.main()
