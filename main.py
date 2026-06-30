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
import subprocess
import threading
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from database import get_active_user_by_stream_key, get_enabled_destinations


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

active_processes: dict[str, subprocess.Popen[Any]] = {}
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


def build_ffmpeg_command(stream_key: str, destinations: list[str]) -> list[str]:
    """Build one FFmpeg command that fans out the input to all enabled outputs."""

    input_url = SRS_INPUT_URL_TEMPLATE.format(stream_key=stream_key)
    command = [
        FFMPEG_BIN,
        "-hide_banner",
        "-loglevel",
        "warning",
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
        process = active_processes.pop(stream_key, None)

    if process is None:
        return False

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
    return True


def monitor_process(stream_key: str, process: subprocess.Popen[Any]) -> None:
    """Remove a worker from active_processes when FFmpeg exits by itself."""

    return_code = process.wait()
    with process_lock:
        current_process = active_processes.get(stream_key)
        if current_process is process:
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

    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    with process_lock:
        active_processes[stream_key] = process

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


@app.get("/active_streams")
def active_streams() -> dict[str, Any]:
    """Expose active stream keys for the Streamlit admin panel."""

    with process_lock:
        streams = [
            {"stream_key": key, "pid": process.pid}
            for key, process in active_processes.items()
            if process.poll() is None
        ]
    return {"code": 0, "streams": streams}


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

    stopped = stop_process(stream_key)
    return JSONResponse(
        status_code=200,
        content={"code": 0, "stream_key": stream_key, "stopped": stopped},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
