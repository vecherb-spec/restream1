"""Real PostgreSQL integration tests.

Requires:
  RESTREAM_TEST_DATABASE_URL=postgresql://...

Recommended launcher:
  ./deploy/scripts/run_postgres_tests.sh
"""

from __future__ import annotations

import os
import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

POSTGRES_URL = os.getenv("RESTREAM_TEST_DATABASE_URL", "").strip()
HAS_POSTGRES = POSTGRES_URL.startswith(("postgresql://", "postgres://"))


@unittest.skipUnless(HAS_POSTGRES, "Set RESTREAM_TEST_DATABASE_URL to run PostgreSQL integration tests")
class PostgresIntegrationTests(unittest.TestCase):
    """Exercise the real database.py layer against PostgreSQL."""

    @classmethod
    def setUpClass(cls) -> None:
        os.environ["DATABASE_URL"] = POSTGRES_URL
        os.environ["RESTREAM_ADMIN_PASSWORD"] = os.getenv(
            "RESTREAM_ADMIN_PASSWORD",
            "test-admin-password-123",
        )
        os.environ["RESTREAM_ALLOW_PLAINTEXT_PASSWORDS"] = "false"
        # Force a clean import of database against Postgres.
        for name in list(sys.modules):
            if name == "database" or name.startswith("database."):
                del sys.modules[name]

        import database

        cls.database = database
        ok, detail = database.check_database()
        if not ok:
            raise unittest.SkipTest(f"PostgreSQL unreachable: {detail}")
        database.init_db()
        if database.DATABASE_BACKEND != "postgres":
            raise AssertionError(f"Expected postgres backend, got {database.DATABASE_BACKEND}")

    def setUp(self) -> None:
        self.db = self.database
        self.suffix = uuid.uuid4().hex[:10]
        self.username = f"pg_{self.suffix}"
        self.password = "password123"
        self.email = f"{self.username}@example.com"

    def test_01_user_creation_login_session_stream_key_title(self) -> None:
        db = self.db
        ok, message, user = db.create_user(self.username, self.password, self.email)
        self.assertTrue(ok, message)
        assert user is not None
        self.assertEqual(user["username"], self.username)
        self.assertTrue(user.get("stream_key"))
        self.assertEqual(db.DATABASE_BACKEND, "postgres")

        auth = db.authenticate_user(self.username, self.password)
        assert auth is not None
        self.assertEqual(int(auth["id"]), int(user["id"]))

        token = db.create_auth_session(int(user["id"]))
        session_user = db.get_user_by_session_token(token)
        assert session_user is not None
        self.assertEqual(session_user["username"], self.username)

        by_key = db.get_active_user_by_stream_key(str(user["stream_key"]))
        assert by_key is not None
        self.assertEqual(int(by_key["id"]), int(user["id"]))

        ok, title_message = db.update_stream_title(int(user["id"]), "PG Night Show")
        self.assertTrue(ok, title_message)
        fresh = db.get_user_by_id(int(user["id"]))
        assert fresh is not None
        self.assertEqual(fresh["stream_title"], "PG Night Show")

        ok, key_message, new_key = db.regenerate_user_stream_key(int(user["id"]))
        self.assertTrue(ok, key_message)
        self.assertTrue(new_key)
        self.assertNotEqual(new_key, user["stream_key"])

        db.delete_auth_session(token)
        self.assertIsNone(db.get_user_by_session_token(token))

    def test_02_destination_settings_and_plan_limits(self) -> None:
        db = self.db
        ok, message, user = db.create_user(self.username, self.password, self.email)
        self.assertTrue(ok, message)
        assert user is not None

        # free plan effective limit is 1 by default
        self.assertEqual(db.get_plan_destination_limit(user), 1)
        self.assertEqual(db.get_user_destination_limit(user), 1)

        db.update_restream_settings(
            int(user["id"]),
            {
                "yt_active": 1,
                "yt_key": "yt-key",
                "vk_active": 1,
                "vk_url": "rtmp://vk.example/app",
                "vk_key": "vk-key",
            },
        )
        fresh = db.get_user_by_id(int(user["id"]))
        assert fresh is not None
        allowed, limit_message = db.validate_destination_limit(fresh)
        self.assertFalse(allowed)
        self.assertIn("1", limit_message)

        ok, plan_message = db.update_user_plan(int(user["id"]), "basic", 3)
        self.assertTrue(ok, plan_message)
        boosted = db.get_user_by_id(int(user["id"]))
        assert boosted is not None
        self.assertEqual(db.get_plan_destination_limit(boosted), 3)
        self.assertEqual(db.get_custom_destination_limit(boosted), 3)
        self.assertEqual(db.get_user_destination_limit(boosted), 3)
        allowed, _ = db.validate_destination_limit(boosted)
        self.assertTrue(allowed)

        # Admin override above plan.
        ok, plan_message = db.update_user_plan(int(user["id"]), "basic", 10)
        self.assertTrue(ok, plan_message)
        overridden = db.get_user_by_id(int(user["id"]))
        assert overridden is not None
        self.assertEqual(db.get_plan_destination_limit(overridden), 3)
        self.assertEqual(db.get_custom_destination_limit(overridden), 10)
        self.assertEqual(db.get_user_destination_limit(overridden), 10)

        specs = db.get_enabled_destination_specs(overridden)
        self.assertEqual({spec["id"] for spec in specs}, {"yt", "vk"})
        self.assertTrue(all(spec["url"].startswith("rtmp://") for spec in specs))

    def test_03_password_reset_and_admin_ops(self) -> None:
        db = self.db
        ok, message, user = db.create_user(self.username, self.password, self.email)
        self.assertTrue(ok, message)
        assert user is not None

        reset_user, token = db.create_password_reset_token(self.username)
        assert reset_user is not None
        assert token is not None
        ok, reset_message = db.reset_password_with_token(token, "newpassword99")
        self.assertTrue(ok, reset_message)
        self.assertIsNone(db.authenticate_user(self.username, self.password))
        auth = db.authenticate_user(self.username, "newpassword99")
        assert auth is not None

        db.set_user_active(int(user["id"]), False)
        self.assertIsNone(db.authenticate_user(self.username, "newpassword99"))
        db.set_user_active(int(user["id"]), True)
        self.assertIsNotNone(db.authenticate_user(self.username, "newpassword99"))

        users = db.list_users()
        self.assertTrue(any(item["username"] == self.username for item in users))

    def test_04_sql_compat_check_database_and_metrics_tables(self) -> None:
        db = self.db
        ok, detail = db.check_database()
        self.assertTrue(ok, detail)
        self.assertEqual(detail, "postgres")

        # Ensure core schema queries work under %s-style adaptation.
        with db.get_connection() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS c FROM users WHERE is_active = ?",
                (1,),
            ).fetchone()
            self.assertIsNotNone(row)
            count = int(row["c"] if isinstance(row, dict) else row[0])
            self.assertGreaterEqual(count, 1)

            # auth_sessions / password_reset_tokens must exist
            connection.execute("SELECT 1 FROM auth_sessions LIMIT 1").fetchone()
            connection.execute("SELECT 1 FROM password_reset_tokens LIMIT 1").fetchone()
            connection.execute("SELECT 1 FROM destination_profiles LIMIT 1").fetchone()

    def test_05_destination_profiles_and_notifications(self) -> None:
        db = self.db
        ok, message, user = db.create_user(self.username, self.password, self.email)
        self.assertTrue(ok, message)
        assert user is not None
        user_id = int(user["id"])

        ok, message, profile = db.create_destination_profile(
            user_id,
            "YouTube Церковь",
            "yt",
            stream_key="pg-yt-secret",
        )
        self.assertTrue(ok, message)
        assert profile is not None
        self.assertEqual(profile["stream_key_masked"], "************")
        self.assertNotIn("stream_key", profile)
        self.assertNotIn("pg-yt-secret", str(profile))

        listed = db.list_destination_profiles(user_id)
        self.assertEqual(len(listed), 1)

        ok, message, updated = db.update_destination_profile(
            user_id,
            int(profile["id"]),
            name="YouTube PG",
            stream_key="************",
        )
        self.assertTrue(ok, message)
        assert updated is not None
        raw = db.get_destination_profile(user_id, int(profile["id"]))
        assert raw is not None
        self.assertEqual(raw["stream_key"], "pg-yt-secret")

        ok, message, applied = db.apply_destination_profile(user_id, int(profile["id"]))
        self.assertTrue(ok, message)
        assert applied is not None
        self.assertEqual(applied.get("yt_key"), "pg-yt-secret")
        self.assertEqual(int(applied.get("yt_profile_id")), int(profile["id"]))

        # Legacy destination without profile_id continues to work.
        db.update_restream_settings(
            user_id,
            {"vk_active": 1, "vk_url": "rtmp://vk.example/app", "vk_key": "vk-legacy", "vk_profile_id": None},
        )
        fresh = db.get_user_by_id(user_id)
        assert fresh is not None
        self.assertIn(fresh.get("vk_profile_id"), (None,))
        specs = db.get_enabled_destination_specs(fresh)
        self.assertTrue(any(spec["id"] == "vk" for spec in specs))

        ok, message, settings = db.update_notification_settings(
            user_id,
            telegram_enabled=True,
            telegram_chat_id="424242",
        )
        self.assertTrue(ok, message)
        self.assertTrue(settings["telegram_enabled"])
        self.assertEqual(settings["telegram_chat_id_masked"], "************")
        self.assertNotIn("424242", str(settings))
        self.assertEqual(db.get_user_telegram_chat_id(user_id), "424242")

        ok, message = db.delete_destination_profile(user_id, int(profile["id"]))
        self.assertTrue(ok, message)
        self.assertEqual(db.list_destination_profiles(user_id), [])


if __name__ == "__main__":
    unittest.main()
