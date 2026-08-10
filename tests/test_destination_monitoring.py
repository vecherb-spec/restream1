"""Destination monitoring isolation and metric formatting tests."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["RESTREAM_DB_PATH"] = str(Path(tempfile.gettempdir()) / "restream-dest-mon-test.db")
os.environ["RESTREAM_ADMIN_PASSWORD"] = "test-admin-password-123"
os.environ["RESTREAM_ALLOW_PLAINTEXT_PASSWORDS"] = "false"
os.environ["RESTREAM_ALLOW_INSECURE_DEFAULTS"] = "true"
os.environ["RESTREAM_SRS_WEBHOOK_SECRET"] = "unit-test-srs-secret-value"


class BitrateParsingTests(unittest.TestCase):
    def test_unknown_bitrate_is_none_not_zero(self) -> None:
        import main

        self.assertIsNone(main.parse_bitrate_kbps(""))
        self.assertIsNone(main.parse_bitrate_kbps("N/A"))
        self.assertIsNone(main.parse_bitrate_kbps(None))

    def test_parses_ffmpeg_bitrate(self) -> None:
        import main

        self.assertAlmostEqual(main.parse_bitrate_kbps("6200.5kbits/s") or 0, 6200.5)
        self.assertAlmostEqual(main.parse_bitrate_kbps("6.2Mbits/s") or 0, 6200.0)


class DestinationMonitoringIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        import main

        main.active_processes.clear()
        main.active_publishers.clear()
        main.ffmpeg_worker_telemetry.clear()
        main.ffmpeg_pending_restarts.clear()
        main.ffmpeg_restart_events.clear()
        main.recent_processes.clear()

    def test_youtube_live_vk_error_isolation(self) -> None:
        import main

        user = {
            "stream_key": "live_user_abcd1234ef01",
            "yt_active": 1,
            "yt_key": "yt-key",
            "vk_active": 1,
            "vk_url": "rtmp://vk.example/app",
            "vk_key": "vk-key",
            "rt_active": 1,
            "rt_url": "rtmp://rt.example/app",
            "rt_key": "rt-key",
            "tg_active": 0,
            "custom_active": 0,
        }
        vk_key = main.worker_key_for("live_user_abcd1234ef01", "vk")
        main.record_worker_error(vk_key, "connection timeout")
        main.worker_telemetry(vk_key)["restart_count"] = 2
        main.worker_telemetry(vk_key)["reconnect_count"] = 4
        yt_key = main.worker_key_for("live_user_abcd1234ef01", "yt")
        main.worker_telemetry(yt_key)["restart_count"] = 0
        main.worker_telemetry(yt_key)["reconnect_count"] = 1

        workers = {
            "yt": {
                "status": "running",
                "platform_id": "yt",
                "uptime_seconds": 100,
                "bitrate_kbps": 6200.0,
                "bitrate": "6200.0kbits/s",
                "fps": 50.0,
                "width": 1920,
                "height": 1080,
                "resolution": "1920x1080",
                "restart_count": 0,
                "reconnect_count": 1,
                "pid": 111,
                "progress_age_seconds": 2,
            },
            "rt": {
                "status": "running",
                "platform_id": "rt",
                "uptime_seconds": 99,
                "bitrate_kbps": 6100.0,
                "fps": 50.0,
                "resolution": "1920x1080",
                "restart_count": 0,
                "reconnect_count": 0,
                "pid": 222,
            },
        }
        statuses = main.build_platform_statuses(
            user,
            {"published_at": "now", "destinations": 3},
            workers,
            {"vk": {"return_code": 1, "platform_id": "vk"}},
        )
        by_id = {item["id"]: item for item in statuses}
        self.assertEqual(by_id["yt"]["state"], "live")
        self.assertEqual(by_id["vk"]["state"], "error")
        self.assertEqual(by_id["rt"]["state"], "live")
        self.assertEqual(by_id["yt"]["restart_count"], 0)
        self.assertEqual(by_id["vk"]["restart_count"], 2)
        self.assertEqual(by_id["vk"]["reconnect_count"], 4)
        self.assertEqual(by_id["vk"]["last_error"], "connection timeout")
        self.assertNotEqual(by_id["yt"]["last_error"], "connection timeout")
        self.assertEqual(by_id["yt"]["uptime_seconds"], 100)
        self.assertEqual(by_id["rt"]["uptime_seconds"], 99)
        self.assertEqual(by_id["yt"]["bitrate_kbps"], 6200.0)
        self.assertIsNone(by_id["vk"].get("bitrate_kbps"))
        # Security: no stream key / RTMP secrets in platform status payload.
        for item in statuses:
            self.assertNotIn("stream_key", item)
            self.assertNotIn("destination_url", item)
            blob = str(item)
            self.assertNotIn("yt-key", blob)
            self.assertNotIn("vk-key", blob)
            self.assertNotIn("rtmp://", blob)

    def test_reconnecting_shows_next_restart(self) -> None:
        import main

        user = {
            "stream_key": "live_user_abcd1234ef01",
            "yt_active": 1,
            "yt_key": "yt-key",
            "vk_active": 0,
            "rt_active": 0,
            "tg_active": 0,
            "custom_active": 0,
        }
        worker_key = main.worker_key_for("live_user_abcd1234ef01", "yt")
        main.set_worker_pending_restart(worker_key, 8, "connection timeout")
        main.record_worker_error(worker_key, "connection timeout")
        statuses = main.build_platform_statuses(
            user,
            {"published_at": "now", "destinations": 1},
            {},
            {},
        )
        yt = next(item for item in statuses if item["id"] == "yt")
        self.assertEqual(yt["state"], "reconnecting")
        self.assertIsNotNone(yt["next_restart_in_seconds"])
        self.assertLessEqual(int(yt["next_restart_in_seconds"]), 8)

    def test_worker_restart_does_not_reset_other_destination(self) -> None:
        import main

        yt = main.worker_key_for("live_a", "yt")
        vk = main.worker_key_for("live_a", "vk")
        main.worker_telemetry(yt)["restart_count"] = 3
        main.worker_telemetry(vk)["restart_count"] = 1
        with main.process_lock:
            main.worker_telemetry(vk)["restart_count"] = int(main.worker_telemetry(vk)["restart_count"]) + 1
        self.assertEqual(main.worker_telemetry(yt)["restart_count"], 3)
        self.assertEqual(main.worker_telemetry(vk)["restart_count"], 2)


if __name__ == "__main__":
    unittest.main()
