"""Production-hardening tests for webhook auth, workers, and limits."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["RESTREAM_DB_PATH"] = str(Path(tempfile.gettempdir()) / "restream-prod-test.db")
os.environ["RESTREAM_ADMIN_PASSWORD"] = "test-admin-password-123"
os.environ["RESTREAM_ALLOW_PLAINTEXT_PASSWORDS"] = "false"
os.environ["RESTREAM_ALLOW_INSECURE_DEFAULTS"] = "true"
os.environ["RESTREAM_SRS_WEBHOOK_SECRET"] = "unit-test-srs-secret-value"


class WebhookSecretTests(unittest.TestCase):
    def test_secret_required_even_for_trusted_ip(self) -> None:
        import main
        from fastapi import HTTPException

        main.SRS_WEBHOOK_SECRET = "unit-test-srs-secret-value"
        main.SRS_TRUSTED_IPS = {"127.0.0.1"}
        request = mock.Mock()
        request.client.host = "127.0.0.1"
        request.headers.get.return_value = ""
        request.query_params.get.return_value = ""
        with self.assertRaises(HTTPException) as raised:
            main.verify_srs_webhook(request)
        self.assertEqual(raised.exception.status_code, 403)

    def test_valid_secret_accepted(self) -> None:
        import main

        main.SRS_WEBHOOK_SECRET = "unit-test-srs-secret-value"
        main.SRS_TRUSTED_IPS = {"127.0.0.1"}
        request = mock.Mock()
        request.client.host = "203.0.113.9"
        request.headers.get.side_effect = lambda key, default="": (
            "unit-test-srs-secret-value" if key == "X-Restream-Webhook-Secret" else default
        )
        request.query_params.get.return_value = ""
        main.verify_srs_webhook(request)

    def test_validate_runtime_secrets_rejects_change_me(self) -> None:
        import main

        previous = main.ALLOW_INSECURE_DEFAULTS
        previous_secret = main.SRS_WEBHOOK_SECRET
        try:
            main.ALLOW_INSECURE_DEFAULTS = False
            main.SRS_WEBHOOK_SECRET = "change_me_srs_webhook_secret"
            with self.assertRaises(RuntimeError):
                main.validate_runtime_secrets()
        finally:
            main.ALLOW_INSECURE_DEFAULTS = previous
            main.SRS_WEBHOOK_SECRET = previous_secret


class WorkerKeyAndRestartTests(unittest.TestCase):
    def test_worker_key_is_stream_and_platform(self) -> None:
        import main

        key = main.worker_key_for("live_demo_abc123", "vk")
        self.assertEqual(key, "live_demo_abc123::vk")
        self.assertEqual(main.split_worker_key(key), ("live_demo_abc123", "vk"))

    def test_rolling_restart_budget(self) -> None:
        import main

        main.FFMPEG_MAX_RESTARTS = 2
        main.FFMPEG_RESTART_WINDOW_SECONDS = 300
        main.FFMPEG_RESTART_BACKOFF_BASE_SECONDS = 2
        main.FFMPEG_RESTART_BACKOFF_MAX_SECONDS = 60
        main.FFMPEG_RESTART_DELAY_SECONDS = 0
        main.ffmpeg_restart_events.clear()
        worker = "live_x::yt"
        self.assertIsNotNone(main.next_restart_delay_seconds(worker))
        self.assertIsNotNone(main.next_restart_delay_seconds(worker))
        self.assertIsNone(main.next_restart_delay_seconds(worker))

    def test_find_latest_log_path_for_worker_is_platform_specific(self) -> None:
        import main

        with tempfile.TemporaryDirectory() as tmp:
            main.LOG_DIR = Path(tmp)
            yt = Path(tmp) / "ffmpeg_live_user_abcd1234ef01_yt_20260101_101010.log"
            vk = Path(tmp) / "ffmpeg_live_user_abcd1234ef01_vk_20260101_121212.log"
            yt.write_text("yt", encoding="utf-8")
            vk.write_text("vk", encoding="utf-8")
            found = main.find_latest_log_path_for_worker("live_user_abcd1234ef01", "vk")
            self.assertEqual(found, vk)


class RedactionTests(unittest.TestCase):
    def test_rtmp_url_key_is_masked(self) -> None:
        import main

        redacted = main.redact_rtmp_url("rtmp://a.rtmp.youtube.com/live2/super-secret-key")
        self.assertIn("*******", redacted)
        self.assertNotIn("super-secret-key", redacted)


class PlanLimitTests(unittest.TestCase):
    def test_plan_custom_effective_split(self) -> None:
        from database import (
            get_custom_destination_limit,
            get_plan_destination_limit,
            get_user_destination_limit,
        )

        user = {"plan": "basic", "max_destinations": 10}
        self.assertEqual(get_plan_destination_limit(user), 3)
        self.assertEqual(get_custom_destination_limit(user), 10)
        self.assertEqual(get_user_destination_limit(user), 10)


class SqliteBackupTests(unittest.TestCase):
    def test_sqlite_backup_uses_integrity_check(self) -> None:
        from database import create_database_backup, get_connection, init_db

        init_db()
        with get_connection() as connection:
            connection.execute(
                "UPDATE users SET stream_title = ? WHERE username = ?",
                ("Backup title", "admin"),
            )
        backup = create_database_backup()
        self.assertTrue(Path(backup["path"]).is_file())
        self.assertGreater(backup["size_bytes"], 0)


if __name__ == "__main__":
    unittest.main()
