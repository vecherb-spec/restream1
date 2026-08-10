"""Worker generation, isolation, orphan recovery, and status tests."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["RESTREAM_DB_PATH"] = str(Path(tempfile.gettempdir()) / "restream-lifecycle-test.db")
os.environ["RESTREAM_ADMIN_PASSWORD"] = "test-admin-password-123"
os.environ["RESTREAM_ALLOW_PLAINTEXT_PASSWORDS"] = "false"
os.environ["RESTREAM_ALLOW_INSECURE_DEFAULTS"] = "true"
os.environ["RESTREAM_SRS_WEBHOOK_SECRET"] = "unit-test-srs-secret-value"


class FakeProcess:
    def __init__(self, pid: int = 4242, returncode: int | None = None) -> None:
        self.pid = pid
        self.returncode = returncode
        self.killed = False
        self.terminated = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


class GenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        import main

        main.active_processes.clear()
        main.active_publishers.clear()
        main.ffmpeg_worker_generations.clear()
        main.ffmpeg_restart_events.clear()
        main.recent_processes.clear()

    def test_stop_worker_invalidates_generation_once(self) -> None:
        import main

        worker = "live_demo_abc123def456::vk"
        main.ffmpeg_worker_generations[worker] = 3
        process = FakeProcess()
        main.active_processes[worker] = {
            "worker_key": worker,
            "stream_key": "live_demo_abc123def456",
            "platform_id": "vk",
            "process": process,
            "started_at": "2026-01-01T00:00:00+00:00",
            "log_path": "/tmp/x.log",
        }
        main.stop_worker(worker)
        self.assertEqual(main.ffmpeg_worker_generations[worker], 4)
        self.assertNotIn(worker, main.active_processes)
        self.assertTrue(process.terminated)

    def test_start_worker_requires_active_publisher(self) -> None:
        import main

        started = main.start_ffmpeg_worker(
            "live_demo_abc123def456",
            {"id": "yt", "title": "YouTube", "url": "rtmp://a.rtmp.youtube.com/live2/key"},
        )
        self.assertFalse(started)

    def test_start_worker_aborts_when_unpublished_after_popen(self) -> None:
        import main

        stream_key = "live_demo_abc123def456"
        main.active_publishers[stream_key] = {"published_at": "now", "destinations": 1}
        fake = FakeProcess(pid=999)

        def fake_popen(*_args, **_kwargs):
            # Simulate concurrent on_unpublish between Popen and registration.
            main.active_publishers.pop(stream_key, None)
            return fake

        with mock.patch("main.subprocess.Popen", side_effect=fake_popen):
            with mock.patch("main.attach_monitor_threads"):
                with mock.patch("main.persist_publishers_state"):
                    started = main.start_ffmpeg_worker(
                        stream_key,
                        {"id": "vk", "title": "VK", "url": "rtmp://vk.example/app/key"},
                    )
        self.assertFalse(started)
        self.assertTrue(fake.killed)
        self.assertNotIn(main.worker_key_for(stream_key, "vk"), main.active_processes)

    def test_destination_status_isolation(self) -> None:
        import main

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
        workers = {
            "yt": {"status": "running", "platform_id": "yt"},
            "vk": {"status": "exited", "platform_id": "vk", "return_code": 1},
        }
        statuses = main.build_platform_statuses(
            user,
            {"published_at": "now", "destinations": 2},
            workers,
            {"vk": {"return_code": 1, "platform_id": "vk"}},
        )
        by_id = {item["id"]: item for item in statuses}
        self.assertEqual(by_id["yt"]["state"], "live")
        self.assertEqual(by_id["vk"]["state"], "error")

    def test_username_log_matcher_accepts_platform_segment(self) -> None:
        import main

        with tempfile.TemporaryDirectory() as tmp:
            main.LOG_DIR = Path(tmp)
            path = Path(tmp) / "ffmpeg_live_alice_abcd1234ef01_vk_20260101_101010.log"
            path.write_text("vk", encoding="utf-8")
            found = main.find_latest_log_path_for_username("alice")
            self.assertEqual(found, path)


class OrphanRecoveryTests(unittest.TestCase):
    def test_adopt_orphan_uses_platform_log(self) -> None:
        import main

        main.active_processes.clear()
        main.active_publishers.clear()
        main.ffmpeg_worker_generations.clear()

        with tempfile.TemporaryDirectory() as tmp:
            main.LOG_DIR = Path(tmp)
            vk_log = Path(tmp) / "ffmpeg_live_user_abcd1234ef01_vk_20260101_121212.log"
            yt_log = Path(tmp) / "ffmpeg_live_user_abcd1234ef01_yt_20260101_101010.log"
            vk_log.write_text("vk", encoding="utf-8")
            yt_log.write_text("yt", encoding="utf-8")

            worker = {
                "pid": 3210,
                "stream_key": "live_user_abcd1234ef01",
                "platform_id": "vk",
                "worker_key": "live_user_abcd1234ef01::vk",
            }
            with mock.patch("main.attach_monitor_threads"):
                with mock.patch("main.persist_publishers_state"):
                    with mock.patch.object(main, "ExternalProcess", return_value=FakeProcess(pid=3210)):
                        adopted = main.adopt_ffmpeg_worker(worker)
            self.assertTrue(adopted)
            entry = main.active_processes["live_user_abcd1234ef01::vk"]
            self.assertEqual(Path(entry["log_path"]), vk_log)
            self.assertNotEqual(Path(entry["log_path"]), yt_log)


class HealthTests(unittest.TestCase):
    def test_health_returns_503_when_db_down(self) -> None:
        import main

        with mock.patch("main.check_database", return_value=(False, "boom")):
            response = main.health()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.body and True, True)
        payload = response.body
        # Starlette JSONResponse stores body as bytes
        import json

        data = json.loads(payload)
        self.assertEqual(data["status"], "degraded")
        self.assertFalse(data["database"]["ok"])


if __name__ == "__main__":
    unittest.main()
