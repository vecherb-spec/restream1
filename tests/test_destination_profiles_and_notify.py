"""Destination profiles, secret masking, and Telegram notification debounce tests."""

from __future__ import annotations

import io
import logging
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_DB_FD, _DB_PATH = tempfile.mkstemp(prefix="restream-profiles-notify-", suffix=".db")
os.close(_DB_FD)
os.environ["RESTREAM_DB_PATH"] = _DB_PATH
os.environ.pop("DATABASE_URL", None)
os.environ["RESTREAM_ADMIN_PASSWORD"] = "test-admin-password-123"
os.environ["RESTREAM_ALLOW_PLAINTEXT_PASSWORDS"] = "false"
os.environ["RESTREAM_ALLOW_INSECURE_DEFAULTS"] = "true"
os.environ["RESTREAM_SRS_WEBHOOK_SECRET"] = "unit-test-srs-secret-value"
os.environ["RESTREAM_TELEGRAM_BOT_TOKEN"] = "123456:TEST-TELEGRAM-BOT-TOKEN-VALUE"


class DestinationProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        import database

        self.db = database
        database.init_db()
        suffix = uuid.uuid4().hex[:8]
        self.username = f"prof_{suffix}"
        ok, message, user = database.create_user(self.username, "password123", f"{self.username}@ex.com")
        self.assertTrue(ok, message)
        assert user is not None
        self.user = user
        self.user_id = int(user["id"])

    def test_create_list_update_delete_profile(self) -> None:
        db = self.db
        ok, message, profile = db.create_destination_profile(
            self.user_id,
            "YouTube Церковь",
            "yt",
            stream_key="yt-secret-key-111",
        )
        self.assertTrue(ok, message)
        assert profile is not None
        self.assertEqual(profile["name"], "YouTube Церковь")
        self.assertEqual(profile["platform_id"], "yt")
        self.assertTrue(profile["has_stream_key"])
        self.assertEqual(profile["stream_key_masked"], "************")
        self.assertNotIn("stream_key", profile)

        listed = db.list_destination_profiles(self.user_id)
        self.assertEqual(len(listed), 1)
        self.assertNotIn("stream_key", listed[0])
        self.assertNotEqual(listed[0].get("stream_key_masked"), "yt-secret-key-111")

        ok, message, updated = db.update_destination_profile(
            self.user_id,
            int(profile["id"]),
            name="YouTube Main",
            stream_key="************",
        )
        self.assertTrue(ok, message)
        assert updated is not None
        self.assertEqual(updated["name"], "YouTube Main")
        raw = db.get_destination_profile(self.user_id, int(profile["id"]))
        assert raw is not None
        self.assertEqual(raw["stream_key"], "yt-secret-key-111")

        ok, message, updated = db.update_destination_profile(
            self.user_id,
            int(profile["id"]),
            stream_key="yt-secret-key-222",
        )
        self.assertTrue(ok, message)
        raw = db.get_destination_profile(self.user_id, int(profile["id"]))
        assert raw is not None
        self.assertEqual(raw["stream_key"], "yt-secret-key-222")

        ok, message = db.delete_destination_profile(self.user_id, int(profile["id"]))
        self.assertTrue(ok, message)
        self.assertEqual(db.list_destination_profiles(self.user_id), [])

    def test_secret_not_returned_by_api_helpers(self) -> None:
        db = self.db
        ok, _message, profile = db.create_destination_profile(
            self.user_id,
            "VK Церковь",
            "vk",
            base_url="rtmp://vk.example/app",
            stream_key="vk-super-secret",
        )
        self.assertTrue(ok)
        assert profile is not None
        serialized = str(profile)
        self.assertNotIn("vk-super-secret", serialized)
        notify = db.get_notification_settings(self.user_id)
        self.assertNotIn(os.environ["RESTREAM_TELEGRAM_BOT_TOKEN"], str(notify))
        self.assertNotIn("stream_key", notify)
        self.assertIn("telegram_chat_id_masked", notify)

    def test_existing_destination_without_profile_still_works(self) -> None:
        db = self.db
        db.update_restream_settings(
            self.user_id,
            {
                "yt_active": 1,
                "yt_key": "legacy-yt-key",
            },
        )
        user = db.get_user_by_id(self.user_id)
        assert user is not None
        self.assertTrue(user.get("yt_active"))
        self.assertEqual(user.get("yt_key"), "legacy-yt-key")
        self.assertIn(user.get("yt_profile_id"), (None, 0, ""))
        specs = db.get_enabled_destination_specs(user)
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0]["id"], "yt")
        self.assertIn("legacy-yt-key", specs[0]["url"])

    def test_apply_profile_copies_into_flat_slot(self) -> None:
        db = self.db
        ok, _message, profile = db.create_destination_profile(
            self.user_id,
            "Rutube Церковь",
            "rt",
            base_url="rtmp://rt.example/live",
            stream_key="rt-key-abc",
        )
        self.assertTrue(ok)
        assert profile is not None
        ok, message, user = db.apply_destination_profile(self.user_id, int(profile["id"]))
        self.assertTrue(ok, message)
        assert user is not None
        self.assertEqual(user.get("rt_url"), "rtmp://rt.example/live")
        self.assertEqual(user.get("rt_key"), "rt-key-abc")
        self.assertEqual(int(user.get("rt_profile_id")), int(profile["id"]))

    def test_unauthorized_user_cannot_read_other_profiles(self) -> None:
        db = self.db
        ok, _message, profile = db.create_destination_profile(
            self.user_id,
            "Private",
            "custom",
            base_url="rtmp://custom.example/app",
            stream_key="custom-secret",
        )
        self.assertTrue(ok)
        assert profile is not None

        ok, _message, other = db.create_user(f"other_{uuid.uuid4().hex[:8]}", "password123", "o@e.com")
        self.assertTrue(ok)
        assert other is not None
        other_id = int(other["id"])
        self.assertEqual(db.list_destination_profiles(other_id), [])
        self.assertIsNone(db.get_destination_profile(other_id, int(profile["id"])))
        ok, message, _profile = db.update_destination_profile(other_id, int(profile["id"]), name="Hack")
        self.assertFalse(ok)
        self.assertIn("не найден", message.lower())
        ok, message = db.delete_destination_profile(other_id, int(profile["id"]))
        self.assertFalse(ok)


class NotificationDebounceTests(unittest.TestCase):
    def setUp(self) -> None:
        import database
        import main

        self.db = database
        self.main = main
        database.init_db()
        main.notify_gate.reset()
        main.notify_tracker.reset()
        main.active_processes.clear()
        main.active_publishers.clear()
        main.ffmpeg_worker_telemetry.clear()
        main.ffmpeg_pending_restarts.clear()
        main.ffmpeg_restart_events.clear()

        suffix = uuid.uuid4().hex[:8]
        ok, message, user = database.create_user(f"ntf_{suffix}", "password123", f"ntf_{suffix}@ex.com")
        self.assertTrue(ok, message)
        assert user is not None
        self.user = user
        self.user_id = int(user["id"])
        self.stream_key = str(user["stream_key"])
        database.update_notification_settings(
            self.user_id,
            telegram_enabled=True,
            telegram_chat_id="999001",
        )
        database.update_restream_settings(
            self.user_id,
            {
                "yt_active": 1,
                "yt_key": "yt-key",
                "vk_active": 1,
                "vk_url": "rtmp://vk.example/app",
                "vk_key": "vk-key",
            },
        )
        ok, plan_message = database.update_user_plan(self.user_id, "basic", 5)
        self.assertTrue(ok, plan_message)

    def test_youtube_error_does_not_change_vk_state(self) -> None:
        main = self.main
        yt_key = main.worker_key_for(self.stream_key, "yt")
        vk_key = main.worker_key_for(self.stream_key, "vk")
        main.record_worker_error(yt_key, "connection timeout")
        main.worker_telemetry(vk_key)["reconnect_count"] = 0
        self.assertEqual(main.worker_telemetry(yt_key)["last_error"], "connection timeout")
        self.assertIsNone(main.worker_telemetry(vk_key).get("last_error"))

        user = {
            "stream_key": self.stream_key,
            "yt_active": 1,
            "yt_key": "yt-key",
            "vk_active": 1,
            "vk_url": "rtmp://vk.example/app",
            "vk_key": "vk-key",
            "rt_active": 0,
            "tg_active": 0,
            "custom_active": 0,
        }
        statuses = main.build_platform_statuses(
            user,
            {"published_at": "2026-01-01T00:00:00+00:00"},
            {
                "vk": {
                    "status": "running",
                    "platform_id": "vk",
                    "uptime_seconds": 40,
                    "bitrate_kbps": 5000.0,
                    "fps": 30.0,
                    "width": 1280,
                    "height": 720,
                    "restart_count": 0,
                    "reconnect_count": 0,
                },
            },
            {"yt": {"return_code": 1, "platform_id": "yt"}},
        )
        by_id = {item["id"]: item for item in statuses}
        self.assertEqual(by_id["yt"]["state"], "error")
        self.assertEqual(by_id["vk"]["state"], "live")

    def test_error_reconnect_recovery_no_spam(self) -> None:
        main = self.main
        sent: list[str] = []

        def fake_send(chat_id: str, text: str, **_kwargs: object) -> tuple[bool, str]:
            sent.append(text)
            self.assertEqual(chat_id, "999001")
            self.assertNotIn(os.environ["RESTREAM_TELEGRAM_BOT_TOKEN"], text)
            return True, "ok"

        yt_key = main.worker_key_for(self.stream_key, "yt")
        with mock.patch.object(main, "send_telegram_message", side_effect=fake_send):
            main.notify_destination_error(yt_key, "connection timeout", restart_in_seconds=8)
            main.notify_destination_error(yt_key, "connection timeout", restart_in_seconds=8)
            main.notify_destination_error(yt_key, "still down", restart_in_seconds=8)
            self.assertEqual(sum(item.startswith("🔴") for item in sent), 1)

            main.notify_destination_reconnecting(yt_key, 8)
            main.notify_destination_reconnecting(yt_key, 8)
            self.assertEqual(sum("началось восстановление" in item for item in sent), 1)

            main.notify_destination_recovered(yt_key)
            main.notify_destination_recovered(yt_key)
            self.assertEqual(sum(item.startswith("🟢") and "восстановлен" in item for item in sent), 1)

        self.assertEqual(len(sent), 3)

    def test_telegram_token_not_in_api_response(self) -> None:
        from fastapi.testclient import TestClient

        main = self.main
        token = self.db.create_auth_session(self.user_id)
        client = TestClient(main.app)
        response = client.get("/api/me/notifications", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        raw = response.text
        self.assertNotIn(os.environ["RESTREAM_TELEGRAM_BOT_TOKEN"], raw)
        self.assertNotIn("TEST-TELEGRAM-BOT-TOKEN-VALUE", raw)
        self.assertTrue(payload.get("telegram_bot_configured"))
        self.assertEqual(payload.get("telegram_chat_id_masked"), "************")
        self.assertNotIn("telegram_bot_token", payload)
        self.assertNotIn("notify_tg_chat_id", payload)

        profiles = client.get("/api/me/destination-profiles", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(profiles.status_code, 200)
        create = client.post(
            "/api/me/destination-profiles",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": "Telegram Церковь",
                "platform_id": "tg",
                "base_url": "rtmps://mux.example/live",
                "stream_key": "tg-secret-xyz",
            },
        )
        self.assertEqual(create.status_code, 200, create.text)
        self.assertNotIn("tg-secret-xyz", create.text)
        self.assertEqual(create.json()["profile"]["stream_key_masked"], "************")

        me = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(me.status_code, 200)
        self.assertNotIn(os.environ["RESTREAM_TELEGRAM_BOT_TOKEN"], me.text)
        self.assertNotIn("999001", me.text)

    def test_telegram_token_not_in_logs(self) -> None:
        import telegram_notify

        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("restream.telegram")
        previous = logger.level
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        try:
            with mock.patch("telegram_notify.urllib.request.urlopen", side_effect=RuntimeError("boom")):
                ok, message = telegram_notify.send_telegram_message("1", "hello")
            self.assertFalse(ok)
            self.assertIn("failed", message.lower())
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous)
        logged = stream.getvalue()
        self.assertNotIn(os.environ["RESTREAM_TELEGRAM_BOT_TOKEN"], logged)
        self.assertNotIn("TEST-TELEGRAM-BOT-TOKEN-VALUE", logged)

    def test_profile_api_crud_and_authz(self) -> None:
        from fastapi.testclient import TestClient

        main = self.main
        token = self.db.create_auth_session(self.user_id)
        client = TestClient(main.app)

        created = client.post(
            "/api/me/destination-profiles",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": "Основной RTMP",
                "platform_id": "custom",
                "base_url": "rtmp://custom.example/live",
                "stream_key": "custom-key-1",
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        profile_id = created.json()["profile"]["id"]

        listed = client.get("/api/me/destination-profiles", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()["profiles"]), 1)

        updated = client.put(
            f"/api/me/destination-profiles/{profile_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "Основной RTMP 2", "stream_key": "************"},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["profile"]["name"], "Основной RTMP 2")

        ok, _message, other = self.db.create_user(f"x_{uuid.uuid4().hex[:8]}", "password123", "x@e.com")
        self.assertTrue(ok)
        assert other is not None
        other_token = self.db.create_auth_session(int(other["id"]))
        foreign = client.get(
            "/api/me/destination-profiles",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        self.assertEqual(foreign.status_code, 200)
        self.assertEqual(foreign.json()["profiles"], [])
        foreign_update = client.put(
            f"/api/me/destination-profiles/{profile_id}",
            headers={"Authorization": f"Bearer {other_token}"},
            json={"name": "stolen"},
        )
        self.assertEqual(foreign_update.status_code, 404)

        deleted = client.delete(
            f"/api/me/destination-profiles/{profile_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(deleted.status_code, 200)


class SqliteBackendSmokeTests(unittest.TestCase):
    def test_sqlite_backend_active(self) -> None:
        import database

        self.assertEqual(database.DATABASE_BACKEND, "sqlite")
        ok, detail = database.check_database()
        self.assertTrue(ok, detail)


if __name__ == "__main__":
    unittest.main()
