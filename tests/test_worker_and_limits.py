"""Lightweight unit tests for destination workers and plan limits."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("RESTREAM_DB_PATH", str(Path(tempfile.gettempdir()) / "restream-test.db"))
os.environ.setdefault("RESTREAM_ADMIN_PASSWORD", "test-admin-password")
os.environ.setdefault("RESTREAM_ALLOW_PLAINTEXT_PASSWORDS", "false")


class DestinationSpecsTests(unittest.TestCase):
    def test_enabled_destination_specs_are_per_platform(self) -> None:
        from database import get_enabled_destination_specs

        user = {
            "yt_active": 1,
            "yt_key": "yt-key",
            "vk_active": 1,
            "vk_url": "rtmp://vk.example/app",
            "vk_key": "vk-key",
            "rt_active": 0,
            "tg_active": 0,
            "custom_active": 0,
        }
        specs = get_enabled_destination_specs(user)
        self.assertEqual([spec["id"] for spec in specs], ["yt", "vk"])
        self.assertTrue(all(spec["url"].startswith("rtmp://") for spec in specs))

    def test_rejects_non_rtmp_urls(self) -> None:
        from database import build_rtmp_target, validate_destination_urls

        self.assertEqual(build_rtmp_target("https://evil.example", "key"), "")
        ok, message = validate_destination_urls(
            {"vk_active": 1, "vk_url": "http://bad", "vk_key": "k"}
        )
        self.assertFalse(ok)
        self.assertIn("rtmp", message.lower())


class PlanLimitTests(unittest.TestCase):
    def test_effective_limit_allows_admin_boost(self) -> None:
        from database import (
            get_custom_destination_limit,
            get_plan_destination_limit,
            get_user_destination_limit,
        )

        user = {"plan": "free", "max_destinations": 5}
        self.assertEqual(get_plan_destination_limit(user), 1)
        self.assertEqual(get_custom_destination_limit(user), 5)
        self.assertEqual(get_user_destination_limit(user), 5)

    def test_plan_default_when_custom_not_higher(self) -> None:
        from database import get_user_destination_limit

        user = {"plan": "pro", "max_destinations": 1}
        self.assertEqual(get_user_destination_limit(user), 5)


class WorkerKeyTests(unittest.TestCase):
    def test_worker_key_helpers(self) -> None:
        import main

        key = main.worker_key_for("live_user_abc", "vk")
        self.assertEqual(key, "live_user_abc::vk")
        stream_key, platform_id = main.split_worker_key(key)
        self.assertEqual(stream_key, "live_user_abc")
        self.assertEqual(platform_id, "vk")
        self.assertTrue(main.worker_key_matches_stream(key, "live_user_abc"))
        self.assertFalse(main.worker_key_matches_stream(key, "live_other"))


class PasswordPolicyTests(unittest.TestCase):
    def test_plaintext_fallback_disabled_by_default(self) -> None:
        from database import verify_password

        self.assertFalse(verify_password("secret", "secret"))

    def test_hashed_password_roundtrip(self) -> None:
        from database import hash_password, verify_password

        stored = hash_password("secret123")
        self.assertTrue(verify_password("secret123", stored))
        self.assertFalse(verify_password("wrong", stored))


class AuthRateLimitTests(unittest.TestCase):
    def test_rate_limit_trips(self) -> None:
        import main
        from fastapi import HTTPException

        main.AUTH_RATE_LIMIT = 2
        main.AUTH_RATE_WINDOW_SECONDS = 60
        main.auth_rate_buckets.clear()

        request = mock.Mock()
        request.client.host = "203.0.113.10"
        request.url.path = "/api/auth/login"

        main.enforce_auth_rate_limit(request)
        main.enforce_auth_rate_limit(request)
        with self.assertRaises(HTTPException) as raised:
            main.enforce_auth_rate_limit(request)
        self.assertEqual(raised.exception.status_code, 429)


if __name__ == "__main__":
    unittest.main()
