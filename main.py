"""FastAPI backend for SRS webhooks and FFmpeg restream workers.

Run locally:
    uvicorn main:app --host 0.0.0.0 --port 8000

SRS should call:
    POST http://127.0.0.1:8000/on_publish
    POST http://127.0.0.1:8000/on_unpublish
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import socket
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from database import DATABASE_PATH, get_active_user_by_stream_key, get_enabled_destinations


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("restream.backend")

app = FastAPI(
    title="Restream MVP Webhooks",
    description="SRS webhook backend that starts/stops FFmpeg pass-through restreaming.",
    version="0.1.0",
)

SRS_INPUT_URL_TEMPLATE = os.getenv(
    "SRS_INPUT_URL_TEMPLATE",
    "rtmp://localhost/live/{stream_key}",
)
FFMPEG_BIN = os.getenv("FFMPEG_BIN", "ffmpeg")
LOG_DIR = Path(os.getenv("RESTREAM_LOG_DIR", "logs"))

active_processes: dict[str, dict[str, Any]] = {}
active_publishers: dict[str, dict[str, Any]] = {}
recent_processes: list[dict[str, Any]] = []
process_lock = threading.Lock()


def srs_error(message: str, status_code: int = 403) -> JSONResponse:
    """Return an error shape that SRS treats as publish rejection."""

    return JSONResponse(
        status_code=status_code,
        content={"code": 1, "message": message},
    )


async def parse_srs_payload(request: Request) -> dict[str, Any]:
    """Parse JSON webhook bodies and shield endpoints from malformed payloads."""

    try:
        payload = await request.json()
    except Exception:
        logger.warning("SRS webhook contained invalid JSON")
        return {}
    return payload if isinstance(payload, dict) else {}


def extract_stream_key(payload: dict[str, Any]) -> str:
    """Extract stream key from SRS payload.

    SRS commonly sends {"stream": "key"}, but some setups include a leading
    slash or a path-like value. The final segment is the OBS stream key.
    """

    raw_stream = str(payload.get("stream") or "").strip()
    if not raw_stream:
        return ""
    return raw_stream.rsplit("/", 1)[-1]


def utc_now_iso() -> str:
    """Return a compact UTC timestamp for API responses."""

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_log_name(stream_key: str) -> str:
    """Create a filesystem-safe log filename from a stream key."""

    safe_key = re.sub(r"[^A-Za-z0-9_.-]+", "_", stream_key).strip("._")
    return safe_key or "stream"


def log_path_for_stream(stream_key: str) -> Path:
    """Return the FFmpeg log path for a stream."""

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return LOG_DIR / f"ffmpeg_{safe_log_name(stream_key)}_{timestamp}.log"


def tail_log_file(log_path: str | Path | None, lines: int = 80) -> list[str]:
    """Read the last N lines of a FFmpeg log file."""

    if not log_path:
        return []

    path = Path(log_path)
    if not path.exists() or not path.is_file():
        return []

    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        logger.exception("Failed to read FFmpeg log file %s", path)
        return []
    return content[-max(1, min(lines, 500)) :]


def parse_ffmpeg_progress(log_path: str | Path | None) -> dict[str, Any]:
    """Parse the latest FFmpeg -progress key/value block from a log file."""

    metrics: dict[str, Any] = {
        "frame": 0,
        "fps": None,
        "bitrate": "",
        "speed": "",
        "out_time_ms": None,
        "progress": "",
    }
    for line in tail_log_file(log_path, lines=500):
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key == "frame":
            try:
                metrics["frame"] = int(value)
            except ValueError:
                metrics["frame"] = 0
        elif key == "fps":
            try:
                metrics["fps"] = float(value)
            except ValueError:
                metrics["fps"] = None
        elif key in {"bitrate", "speed", "progress"}:
            metrics[key] = value
        elif key == "out_time_ms":
            try:
                metrics["out_time_ms"] = int(value)
            except ValueError:
                metrics["out_time_ms"] = None
    return metrics


def process_snapshot(stream_key: str, entry: dict[str, Any]) -> dict[str, Any]:
    """Serialize a process entry for the admin API."""

    process: subprocess.Popen[Any] = entry["process"]
    return_code = process.poll()
    progress = parse_ffmpeg_progress(entry.get("log_path"))
    return {
        "stream_key": stream_key,
        "pid": process.pid,
        "status": "running" if return_code is None else "exited",
        "return_code": return_code,
        "started_at": entry.get("started_at"),
        "destinations": entry.get("destinations", 0),
        "log_path": str(entry.get("log_path") or ""),
        "frame": progress["frame"],
        "fps": progress["fps"],
        "bitrate": progress["bitrate"],
        "speed": progress["speed"],
        "progress": progress["progress"],
    }


def stream_status_payload(stream_key: str) -> dict[str, Any]:
    """Build the client-facing stream status payload."""

    with process_lock:
        publisher = active_publishers.get(stream_key)
        process_entry = active_processes.get(stream_key)
        recent_entry = next(
            (item for item in recent_processes if item.get("stream_key") == stream_key),
            None,
        )

    process = process_snapshot(stream_key, process_entry) if process_entry else None
    recent = recent_entry if recent_entry else None
    destinations = 0
    if publisher:
        destinations = int(publisher.get("destinations") or 0)
    elif process:
        destinations = int(process.get("destinations") or 0)

    frame = int(process.get("frame") or 0) if process else 0
    if not publisher:
        color = "red"
        label = "Нет входящего потока"
        message = "OBS не публикует поток в SRS или SRS еще не прислал on_publish."
    elif destinations == 0:
        color = "yellow"
        label = "Поток в SRS, рестрим не запущен"
        message = "Входящий поток есть, но активные площадки не настроены."
    elif process and process.get("status") == "running":
        color = "green" if frame > 0 else "yellow"
        label = "Рестрим работает" if frame > 0 else "FFmpeg запущен, ждем кадры"
        message = "FFmpeg отправляет поток на активные площадки."
    else:
        color = "yellow"
        label = "Поток есть, FFmpeg не работает"
        message = "Проверьте FFmpeg-лог и настройки площадок."

    return {
        "code": 0,
        "stream_key": stream_key,
        "color": color,
        "label": label,
        "message": message,
        "publisher": publisher,
        "process": process,
        "recent": recent,
        "frame": frame,
        "fps": process.get("fps") if process else None,
        "bitrate": process.get("bitrate") if process else "",
        "speed": process.get("speed") if process else "",
        "destinations": destinations,
    }


def read_memory_metrics() -> dict[str, Any]:
    """Read Linux memory metrics from /proc/meminfo."""

    values: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, raw_value = line.split(":", 1)
            values[key] = int(raw_value.strip().split()[0]) * 1024
    except (OSError, ValueError):
        return {}

    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", 0)
    used = max(total - available, 0)
    used_percent = round((used / total) * 100, 1) if total else 0
    return {
        "total_bytes": total,
        "available_bytes": available,
        "used_bytes": used,
        "used_percent": used_percent,
    }


def check_tcp_port(host: str, port: int, timeout: float = 0.5) -> bool:
    """Return True if a TCP port accepts connections."""

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def system_metrics_payload() -> dict[str, Any]:
    """Collect lightweight server metrics without extra dependencies."""

    load_1, load_5, load_15 = os.getloadavg()
    cpu_count = os.cpu_count() or 1
    disk = shutil.disk_usage(DATABASE_PATH.parent)
    with process_lock:
        ffmpeg_count = len(active_processes)
        publisher_count = len(active_publishers)

    return {
        "code": 0,
        "timestamp": utc_now_iso(),
        "cpu": {
            "cores": cpu_count,
            "load_1": round(load_1, 2),
            "load_5": round(load_5, 2),
            "load_15": round(load_15, 2),
            "load_1_per_core": round(load_1 / cpu_count, 2),
        },
        "memory": read_memory_metrics(),
        "disk": {
            "path": str(DATABASE_PATH.parent),
            "total_bytes": disk.total,
            "used_bytes": disk.used,
            "free_bytes": disk.free,
            "used_percent": round((disk.used / disk.total) * 100, 1) if disk.total else 0,
        },
        "processes": {
            "ffmpeg_active": ffmpeg_count,
            "publishers_active": publisher_count,
        },
        "services": {
            "api": True,
            "srs_rtmp_1935": check_tcp_port("127.0.0.1", 1935),
            "srs_hls_8080": check_tcp_port("127.0.0.1", 8080),
        },
    }


def build_ffmpeg_command(stream_key: str, destinations: list[str]) -> list[str]:
    """Build one FFmpeg command that fans out the input to all enabled outputs."""

    input_url = SRS_INPUT_URL_TEMPLATE.format(stream_key=stream_key)
    command = [
        FFMPEG_BIN,
        "-hide_banner",
        "-nostats",
        "-loglevel",
        "warning",
        "-progress",
        "pipe:2",
        "-i",
        input_url,
        "-c",
        "copy",
    ]
    for destination in destinations:
        command.extend(["-f", "flv", destination])
    return command


def stop_process(stream_key: str) -> bool:
    """Terminate a running FFmpeg worker for a stream key."""

    with process_lock:
        entry = active_processes.pop(stream_key, None)

    if entry is None:
        return False

    process: subprocess.Popen[Any] = entry["process"]
    if process.poll() is not None:
        logger.info("FFmpeg for %s already exited with code %s", stream_key, process.returncode)
        return True

    logger.info("Stopping FFmpeg for stream %s", stream_key)
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        logger.warning("FFmpeg for %s did not stop gracefully; killing it", stream_key)
        process.kill()
        process.wait(timeout=5)

    snapshot = process_snapshot(stream_key, entry)
    snapshot["ended_at"] = utc_now_iso()
    snapshot["stopped_by"] = "admin_or_webhook"
    with process_lock:
        recent_processes.insert(0, snapshot)
        del recent_processes[50:]
    return True


def monitor_process(stream_key: str, process: subprocess.Popen[Any]) -> None:
    """Remove a worker from active_processes when FFmpeg exits by itself."""

    return_code = process.wait()
    with process_lock:
        current_entry = active_processes.get(stream_key)
        if current_entry and current_entry.get("process") is process:
            snapshot = process_snapshot(stream_key, current_entry)
            snapshot["ended_at"] = utc_now_iso()
            recent_processes.insert(0, snapshot)
            del recent_processes[50:]
            active_processes.pop(stream_key, None)

    if return_code == 0:
        logger.info("FFmpeg for %s finished successfully", stream_key)
    else:
        logger.error("FFmpeg for %s exited with code %s", stream_key, return_code)


def start_ffmpeg(stream_key: str, destinations: list[str]) -> bool:
    """Start or replace a FFmpeg worker for the stream key."""

    if not destinations:
        logger.info("Stream %s accepted without restream destinations", stream_key)
        return False

    # SRS may retry callbacks; ensure only one worker exists per stream key.
    stop_process(stream_key)

    command = build_ffmpeg_command(stream_key, destinations)
    redacted_command = command[:]
    for index, value in enumerate(redacted_command):
        if value.startswith("rtmp://") and index > 0:
            redacted_command[index] = "[RTMP_OUTPUT_REDACTED]"
    logger.info("Starting FFmpeg for %s: %s", stream_key, " ".join(redacted_command))

    log_path = log_path_for_stream(stream_key)
    with log_path.open("ab") as log_file:
        log_file.write(f"[{utc_now_iso()}] Starting: {' '.join(redacted_command)}\n".encode("utf-8"))
        log_file.flush()
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=log_file,
        )

    with process_lock:
        active_processes[stream_key] = {
            "process": process,
            "started_at": utc_now_iso(),
            "destinations": len(destinations),
            "log_path": log_path,
        }

    threading.Thread(
        target=monitor_process,
        args=(stream_key, process),
        name=f"ffmpeg-monitor-{stream_key}",
        daemon=True,
    ).start()
    return True


@app.get("/health")
def health() -> dict[str, Any]:
    """Simple readiness endpoint."""

    with process_lock:
        active_count = len(active_processes)
    return {"status": "ok", "active_streams": active_count}


@app.get("/system_metrics")
def system_metrics() -> dict[str, Any]:
    """Expose lightweight server metrics for the admin dashboard."""

    return system_metrics_payload()


@app.get("/active_streams")
def active_streams() -> dict[str, Any]:
    """Expose active stream keys for the Streamlit admin panel."""

    with process_lock:
        streams = [process_snapshot(key, entry) for key, entry in active_processes.items()]
        publishers = [
            {"stream_key": key, **value}
            for key, value in active_publishers.items()
        ]
        recent = recent_processes[:20]
    return {"code": 0, "streams": streams, "publishers": publishers, "recent": recent}


@app.get("/stream_status/{stream_key}")
def stream_status(stream_key: str) -> dict[str, Any]:
    """Expose client-facing live status for one stream key."""

    return stream_status_payload(stream_key)


@app.get("/stream_logs/{stream_key}")
def stream_logs(stream_key: str, lines: int = 80) -> dict[str, Any]:
    """Return the latest FFmpeg log lines for an active stream."""

    with process_lock:
        entry = active_processes.get(stream_key)
        recent_entry = next(
            (item for item in recent_processes if item.get("stream_key") == stream_key),
            None,
        )

    log_path = entry.get("log_path") if entry else None
    if log_path is None and recent_entry:
        log_path = recent_entry.get("log_path")

    if log_path is None:
        return {"code": 1, "message": "stream log was not found", "lines": []}

    return {
        "code": 0,
        "stream_key": stream_key,
        "log_path": str(log_path or ""),
        "lines": tail_log_file(log_path, lines=lines),
    }


@app.post("/stop_stream/{stream_key}")
def stop_stream(stream_key: str) -> dict[str, Any]:
    """Allow the admin panel to stop a FFmpeg worker without waiting for SRS."""

    stopped = stop_process(stream_key)
    return {"code": 0, "stream_key": stream_key, "stopped": stopped}


@app.post("/on_publish")
async def on_publish(request: Request) -> JSONResponse:
    """Authorize SRS publishing and start FFmpeg fan-out for enabled platforms."""

    payload = await parse_srs_payload(request)
    stream_key = extract_stream_key(payload)
    if not stream_key:
        return srs_error("stream key is required", status_code=400)

    user = get_active_user_by_stream_key(stream_key)
    if user is None:
        logger.warning("Rejected publish for inactive or unknown stream key: %s", stream_key)
        return srs_error("stream is not allowed")

    destinations = get_enabled_destinations(user)
    try:
        started = start_ffmpeg(stream_key, destinations)
    except FileNotFoundError:
        logger.exception("FFmpeg binary was not found")
        return srs_error("ffmpeg is not installed", status_code=500)
    except Exception as exc:
        logger.exception("Failed to start FFmpeg for %s", stream_key)
        return srs_error(f"failed to start restream: {exc}", status_code=500)

    with process_lock:
        active_publishers[stream_key] = {
            "published_at": utc_now_iso(),
            "destinations": len(destinations),
            "ffmpeg_started": started,
        }

    return JSONResponse(
        status_code=200,
        content={
            "code": 0,
            "stream_key": stream_key,
            "ffmpeg_started": started,
            "destinations": len(destinations),
        },
    )


@app.post("/on_unpublish")
async def on_unpublish(request: Request) -> JSONResponse:
    """Stop the FFmpeg process when SRS reports stream unpublish."""

    payload = await parse_srs_payload(request)
    stream_key = extract_stream_key(payload)
    if not stream_key:
        return srs_error("stream key is required", status_code=400)

    with process_lock:
        active_publishers.pop(stream_key, None)
    stopped = stop_process(stream_key)
    return JSONResponse(
        status_code=200,
        content={"code": 0, "stream_key": stream_key, "stopped": stopped},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
