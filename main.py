"""FastAPI backend for SRS webhooks and FFmpeg restream workers.

Run locally:
    uvicorn main:app --host 0.0.0.0 --port 8000

SRS should call:
    POST http://127.0.0.1:8000/on_publish?token=YOUR_SRS_WEBHOOK_SECRET
    POST http://127.0.0.1:8000/on_unpublish?token=YOUR_SRS_WEBHOOK_SECRET
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(dotenv_path: str | Path | None = None, **_kwargs: object) -> bool:
        env_path = Path(dotenv_path or ".env")
        if not env_path.is_file():
            return False
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
        return True

load_dotenv(Path(__file__).resolve().parent / ".env")

import re
import secrets
import shutil
import signal
import socket
import smtplib
import subprocess
import threading
import time
import json
import asyncio
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from fastapi import Cookie, Depends, Header, HTTPException, Request, Response
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from starlette.requests import Request as StarletteRequest

from database import (
    DATABASE_BACKEND,
    DATABASE_PATH,
    authenticate_user,
    change_user_password,
    check_database,
    create_auth_session,
    create_database_backup,
    create_password_reset_token,
    create_user,
    delete_auth_session,
    get_active_user_by_stream_key,
    get_enabled_destinations,
    get_user_by_id,
    get_user_by_session_token,
    init_db,
    list_database_backups,
    list_users,
    regenerate_user_stream_key,
    reset_password_with_token,
    set_user_active,
    update_user_plan,
    update_stream_title,
    update_restream_settings,
    update_user_password,
    validate_destination_limit,
    validate_destination_urls,
)


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

CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "RESTREAM_CORS_ORIGINS",
        "http://localhost:3000,https://restream.medialive.ru",
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    """Ensure schema migrations run and recover orphan FFmpeg workers."""

    try:
        init_db()
        ok, detail = check_database()
        if ok:
            logger.info("Database ready (%s)", detail)
        else:
            logger.error("Database is unavailable on startup: %s", detail)
    except Exception:
        logger.exception("Database startup check failed")

    try:
        recovery = recover_ffmpeg_workers_on_startup()
        logger.info(
            "FFmpeg worker recovery: adopted=%s killed=%s restarted=%s",
            recovery.get("adopted", 0),
            recovery.get("killed", 0),
            recovery.get("restarted", 0),
        )
    except Exception:
        logger.exception("FFmpeg worker recovery failed on startup")


@app.exception_handler(HTTPException)
async def http_exception_handler(_request: StarletteRequest, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: StarletteRequest, exc: Exception) -> JSONResponse:
    """Log unexpected server errors instead of failing silently."""

    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})

SRS_INPUT_URL_TEMPLATE = os.getenv(
    "SRS_INPUT_URL_TEMPLATE",
    "rtmp://localhost/live/{stream_key}",
)
FFMPEG_BIN = os.getenv("FFMPEG_BIN", "ffmpeg")
FFPROBE_BIN = os.getenv("FFPROBE_BIN", "ffprobe")
FFMPEG_MAX_RESTARTS = max(0, int(os.getenv("RESTREAM_FFMPEG_MAX_RESTARTS", "5")))
FFMPEG_RESTART_DELAY_SECONDS = max(0.0, float(os.getenv("RESTREAM_FFMPEG_RESTART_DELAY_SECONDS", "3")))
# Network IO timeout for RTMP read/write (microseconds). Default: 15s.
FFMPEG_RW_TIMEOUT_US = max(1_000_000, int(os.getenv("RESTREAM_FFMPEG_RW_TIMEOUT_US", "15000000")))
# Kill FFmpeg if -progress stops advancing. 0 disables stall watchdog.
FFMPEG_STALL_TIMEOUT_SECONDS = max(0.0, float(os.getenv("RESTREAM_FFMPEG_STALL_TIMEOUT_SECONDS", "45")))
FFMPEG_STALL_GRACE_SECONDS = max(0.0, float(os.getenv("RESTREAM_FFMPEG_STALL_GRACE_SECONDS", "30")))
# adopt = keep orphan workers and reattach monitors; kill = terminate orphans on boot
FFMPEG_ORPHAN_POLICY = os.getenv("RESTREAM_FFMPEG_ORPHAN_POLICY", "adopt").strip().lower()
LOG_DIR = Path(os.getenv("RESTREAM_LOG_DIR", "logs"))
STATE_DIR = Path(os.getenv("RESTREAM_STATE_DIR", "state"))
PUBLISHERS_STATE_PATH = STATE_DIR / "active_publishers.json"
COOKIE_NAME = os.getenv("RESTREAM_COOKIE_NAME", "restream_session")
COOKIE_MAX_AGE_SECONDS = int(os.getenv("RESTREAM_AUTH_SESSION_DAYS", "7")) * 24 * 60 * 60
COOKIE_SECURE = os.getenv("RESTREAM_COOKIE_SECURE", "true").lower() == "true"
COOKIE_DOMAIN = os.getenv("RESTREAM_COOKIE_DOMAIN", "").strip() or None
PUBLIC_BASE_URL = os.getenv("RESTREAM_PUBLIC_URL", "https://restream.medialive.ru").rstrip("/")
SMTP_HOST = os.getenv("RESTREAM_SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("RESTREAM_SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("RESTREAM_SMTP_USERNAME", "").strip()
SMTP_PASSWORD = os.getenv("RESTREAM_SMTP_PASSWORD", "").strip()
SMTP_FROM = os.getenv("RESTREAM_SMTP_FROM", SMTP_USERNAME or "no-reply@restream.medialive.ru").strip()
SMTP_USE_TLS = os.getenv("RESTREAM_SMTP_TLS", "true").lower() == "true"
SMTP_USE_SSL = os.getenv("RESTREAM_SMTP_SSL", "false").lower() == "true" or SMTP_PORT == 465
SMTP_TIMEOUT = int(os.getenv("RESTREAM_SMTP_TIMEOUT", "20"))
SRS_WEBHOOK_SECRET = os.getenv("RESTREAM_SRS_WEBHOOK_SECRET", "").strip()
SRS_TRUSTED_IPS = {
    ip.strip()
    for ip in os.getenv("RESTREAM_SRS_TRUSTED_IPS", "127.0.0.1,::1").split(",")
    if ip.strip()
}

active_processes: dict[str, dict[str, Any]] = {}
active_publishers: dict[str, dict[str, Any]] = {}
recent_processes: list[dict[str, Any]] = []
ffmpeg_restart_counts: dict[str, int] = {}
ffmpeg_worker_generations: dict[str, int] = {}
process_lock = threading.Lock()


class LoginPayload(BaseModel):
    username: str
    password: str


class RegisterPayload(BaseModel):
    username: str
    password: str
    email: str = ""


class RestreamSettingsPayload(BaseModel):
    yt_active: bool | None = None
    yt_key: str | None = None
    vk_active: bool | None = None
    vk_url: str | None = None
    vk_key: str | None = None
    rt_active: bool | None = None
    rt_url: str | None = None
    rt_key: str | None = None
    tg_active: bool | None = None
    tg_url: str | None = None
    tg_key: str | None = None
    custom_active: bool | None = None
    custom_url: str | None = None
    custom_key: str | None = None
    stream_title: str | None = None


class PasswordChangePayload(BaseModel):
    current_password: str
    new_password: str


class ForgotPasswordPayload(BaseModel):
    identifier: str


class PasswordResetPayload(BaseModel):
    token: str
    new_password: str


class StreamTitlePayload(BaseModel):
    stream_title: str


class AdminPasswordPayload(BaseModel):
    new_password: str


class ActivePayload(BaseModel):
    is_active: bool


class PlanPayload(BaseModel):
    plan: str
    max_destinations: int


def public_user(user: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a user payload safe enough for API responses."""

    if user is None:
        return None
    return {key: value for key, value in user.items() if key != "password"}


def set_session_cookie(response: Response, token: str) -> None:
    """Set the HttpOnly browser session cookie used by the Next.js frontend."""

    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=COOKIE_MAX_AGE_SECONDS,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        domain=COOKIE_DOMAIN,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    """Clear the browser session cookie."""

    response.delete_cookie(
        key=COOKIE_NAME,
        domain=COOKIE_DOMAIN,
        path="/",
    )


def create_token_response(user: dict[str, Any], response: Response | None = None) -> dict[str, Any]:
    """Create API auth response and optionally attach the cookie session."""

    token = create_auth_session(int(user["id"]))
    if response is not None:
        set_session_cookie(response, token)
    return {"code": 0, "token": token, "user": public_user(user)}


def extract_bearer_token(authorization: str | None) -> str | None:
    """Extract a Bearer token from an Authorization header."""

    if not authorization:
        return None

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token.strip()


def get_current_api_user(
    authorization: str | None = Header(default=None),
    session_cookie: str | None = Cookie(default=None, alias=COOKIE_NAME),
) -> dict[str, Any]:
    """Resolve the current API user from an HttpOnly cookie or Bearer token."""

    cookie_token = session_cookie if isinstance(session_cookie, str) else None
    token = cookie_token or extract_bearer_token(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Session cookie or Bearer token is required")
    user = get_user_by_session_token(token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return user


def get_current_admin(user: dict[str, Any] = Depends(get_current_api_user)) -> dict[str, Any]:
    """Require an authenticated administrator."""

    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role is required")
    return user


def get_request_client_ip(request: Request) -> str:
    """Return the direct peer IP for webhook auth (never trust X-Forwarded-For)."""

    # Client-controlled X-Forwarded-For must not authorize SRS webhooks.
    return request.client.host if request.client else ""


def verify_srs_webhook(request: Request) -> None:
    """Allow SRS hooks only from trusted direct peers or with a shared secret."""

    client_ip = get_request_client_ip(request)
    if client_ip in SRS_TRUSTED_IPS:
        return

    if SRS_WEBHOOK_SECRET:
        provided = request.headers.get("X-Restream-Webhook-Secret", "")
        if not provided:
            provided = request.query_params.get("token", "")
        if provided and secrets.compare_digest(provided, SRS_WEBHOOK_SECRET):
            return

    logger.warning("Rejected SRS webhook from %s", client_ip)
    raise HTTPException(status_code=403, detail="SRS webhook is not allowed")


def model_to_dict(model: BaseModel, exclude_unset: bool = False) -> dict[str, Any]:
    """Return model data for both Pydantic v1 and v2."""

    if hasattr(model, "model_dump"):
        return model.model_dump(exclude_unset=exclude_unset)
    return model.dict(exclude_unset=exclude_unset)


def password_reset_url(token: str) -> str:
    """Build public password reset URL for the Next.js app."""

    return f"{PUBLIC_BASE_URL}/?reset_token={token}"


def send_password_reset_email(user: dict[str, Any], token: str) -> dict[str, Any]:
    """Send password reset email. Never expose reset URLs to API clients."""

    email = str(user.get("email") or "").strip()
    reset_url = password_reset_url(token)
    generic_message = (
        "Если аккаунт существует и для него настроен email, "
        "мы отправили инструкции по восстановлению пароля."
    )
    if not email:
        logger.info(
            "Password reset requested for %s without email. Reset URL (server-only): %s",
            user.get("username"),
            reset_url,
        )
        return {"delivery": "none", "message": generic_message}

    if not SMTP_HOST:
        logger.warning(
            "SMTP is not configured. Password reset URL for %s <%s> (server-only): %s",
            user.get("username"),
            email,
            reset_url,
        )
        return {"delivery": "none", "message": generic_message}

    message = EmailMessage()
    message["Subject"] = "Восстановление пароля MediaLive"
    message["From"] = SMTP_FROM
    message["To"] = email
    message.set_content(
        "\n".join(
            [
                f"Здравствуйте, {user.get('username')}.",
                "",
                "Для восстановления пароля перейдите по ссылке:",
                reset_url,
                "",
                "Ссылка действует 1 час. Если вы не запрашивали восстановление, просто проигнорируйте письмо.",
            ]
        )
    )

    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT) if SMTP_USE_SSL else smtplib.SMTP(
            SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT
        ) as smtp:
            if not SMTP_USE_SSL and SMTP_USE_TLS:
                smtp.starttls()
            if SMTP_USERNAME:
                smtp.login(SMTP_USERNAME, SMTP_PASSWORD)
            smtp.send_message(message)
    except Exception:
        logger.exception(
            "Failed to send password reset email for %s. Reset URL (server-only): %s",
            user.get("username"),
            reset_url,
        )
        return {"delivery": "none", "message": generic_message}

    logger.info("Password reset email sent to %s for user %s", email, user.get("username"))
    return {"delivery": "email", "message": generic_message}


def build_pending_user_settings(user: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    """Merge current user row with pending restream settings."""

    pending_user = dict(user)
    for key, value in settings.items():
        if key.endswith("_active"):
            pending_user[key] = 1 if bool(value) else 0
        elif isinstance(value, str):
            pending_user[key] = value.strip()
        else:
            pending_user[key] = value
    return pending_user


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
    slash or a path-like value. The final segment is the VideoCoder stream key.
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


def progress_path_for_log(log_path: Path) -> Path:
    """Return the sidecar progress file path for an FFmpeg log."""

    return log_path.with_name(f"{log_path.name}.progress")


def find_latest_log_path_for_stream(stream_key: str) -> Path | None:
    """Find the newest FFmpeg log file for a stream key on disk."""

    safe_key = safe_log_name(stream_key)
    if not LOG_DIR.exists() or not LOG_DIR.is_dir():
        return None

    candidates = [
        path
        for path in LOG_DIR.glob(f"ffmpeg_{safe_key}_*.log")
        if path.is_file()
    ]
    if not candidates:
        return None

    try:
        return max(candidates, key=lambda path: path.stat().st_mtime)
    except OSError:
        return None


def find_latest_log_path_for_username(username: str) -> Path | None:
    """Find the newest FFmpeg log for stream keys belonging to this username only."""

    safe_username = "".join(
        char.lower() if char.isalnum() else "_" for char in (username or "user")
    ).strip("_")
    safe_username = safe_username or "user"
    if not LOG_DIR.exists() or not LOG_DIR.is_dir():
        return None

    # Require live_{username}_{12hex}_{timestamp}.log so "a" cannot match "alice".
    name_re = re.compile(
        rf"^ffmpeg_live_{re.escape(safe_username)}_[a-f0-9]{{12}}_\d{{8}}_\d{{6}}\.log$"
    )
    candidates = [
        path
        for path in LOG_DIR.glob(f"ffmpeg_live_{safe_username}_*.log")
        if path.is_file() and name_re.match(path.name)
    ]
    if not candidates:
        return None

    try:
        return max(candidates, key=lambda path: path.stat().st_mtime)
    except OSError:
        return None


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


def parse_ffmpeg_progress(progress_path: str | Path | None) -> dict[str, Any]:
    """Parse the latest FFmpeg -progress key/value block from a progress file."""

    metrics: dict[str, Any] = {
        "frame": 0,
        "fps": None,
        "bitrate": "",
        "speed": "",
        "dropped_frames": None,
        "out_time_ms": None,
        "progress": "",
    }
    for line in tail_log_file(progress_path, lines=500):
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
        elif key in {"drop_frames", "dropped_frames"}:
            try:
                metrics["dropped_frames"] = int(value)
            except ValueError:
                metrics["dropped_frames"] = None
        elif key == "out_time_ms":
            try:
                metrics["out_time_ms"] = int(value)
            except ValueError:
                metrics["out_time_ms"] = None
    return metrics


def process_snapshot(stream_key: str, entry: dict[str, Any]) -> dict[str, Any]:
    """Serialize a process entry for the admin API."""

    process: Any = entry["process"]
    return_code = process.poll()
    progress_path = entry.get("progress_path") or (
        progress_path_for_log(Path(entry["log_path"])) if entry.get("log_path") else None
    )
    progress = parse_ffmpeg_progress(progress_path)
    return {
        "stream_key": stream_key,
        "pid": process.pid,
        "status": "running" if return_code is None else "exited",
        "return_code": return_code,
        "started_at": entry.get("started_at"),
        "destinations": entry.get("destinations", 0),
        "log_path": str(entry.get("log_path") or ""),
        "progress_path": str(progress_path or ""),
        "frame": progress["frame"],
        "fps": progress["fps"],
        "bitrate": progress["bitrate"],
        "speed": progress["speed"],
        "dropped_frames": progress["dropped_frames"],
        "resolution": entry.get("resolution") or "",
        "progress": progress["progress"],
    }


PLATFORM_STATUS_CONFIGS = [
    {
        "id": "yt",
        "title": "YouTube",
        "active_field": "yt_active",
        "key_field": "yt_key",
        "url_field": None,
        "fixed_url": True,
    },
    {
        "id": "vk",
        "title": "VK",
        "active_field": "vk_active",
        "key_field": "vk_key",
        "url_field": "vk_url",
        "fixed_url": False,
    },
    {
        "id": "rt",
        "title": "Rutube",
        "active_field": "rt_active",
        "key_field": "rt_key",
        "url_field": "rt_url",
        "fixed_url": False,
    },
    {
        "id": "tg",
        "title": "Telegram",
        "active_field": "tg_active",
        "key_field": "tg_key",
        "url_field": "tg_url",
        "fixed_url": False,
    },
    {
        "id": "custom",
        "title": "Custom RTMP",
        "active_field": "custom_active",
        "key_field": "custom_key",
        "url_field": "custom_url",
        "fixed_url": False,
    },
]


def platform_configured(user: dict[str, Any], config: dict[str, Any]) -> bool:
    """Return whether a platform has enough data to build a restream destination."""

    has_key = bool(str(user.get(config["key_field"]) or "").strip())
    if config.get("fixed_url"):
        return has_key
    url_field = config.get("url_field")
    has_url = bool(str(user.get(url_field) or "").strip()) if url_field else False
    return has_key and has_url


def build_platform_statuses(
    user: dict[str, Any] | None,
    publisher: dict[str, Any] | None,
    process: dict[str, Any] | None,
    recent: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Build client-facing per-platform status labels."""

    if user is None:
        return []

    process_running = bool(process and process.get("status") == "running")
    process_exited = bool(process and process.get("status") == "exited")
    recent_failed = bool(recent and recent.get("return_code") not in {None, 0})

    statuses: list[dict[str, Any]] = []
    for config in PLATFORM_STATUS_CONFIGS:
        active = bool(user.get(config["active_field"]))
        configured = platform_configured(user, config)

        if not configured:
            state = "not_configured"
            label = "Не настроена"
            color = "red" if active else "gray"
            reason = "Заполните RTMP URL и stream key." if active else ""
        elif not active:
            state = "stopped"
            label = "Остановлена"
            color = "gray"
            reason = ""
        elif not publisher:
            state = "waiting_input"
            label = "Ждет VideoCoder"
            color = "yellow"
            reason = "Входящий поток пока не опубликован в SRS."
        elif process_running:
            state = "live"
            label = "В эфире"
            color = "green"
            reason = ""
        elif process_exited or recent_failed:
            state = "error"
            label = "Ошибка"
            color = "red"
            reason = "FFmpeg завершился. Откройте ошибки рестрима."
        else:
            state = "starting"
            label = "Запускается"
            color = "yellow"
            reason = "FFmpeg еще не отдал статус."

        statuses.append(
            {
                "id": config["id"],
                "title": config["title"],
                "active": active,
                "configured": configured,
                "state": state,
                "label": label,
                "color": color,
                "reason": reason,
            }
        )

    return statuses


def probe_stream_resolution(stream_key: str, input_url: str) -> None:
    """Probe the RTMP input once and store video resolution for status UI."""

    try:
        result = subprocess.run(
            [
                FFPROBE_BIN,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "json",
                input_url,
            ],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        logger.warning("Could not probe resolution for %s", stream_key)
        return

    try:
        data = json.loads(result.stdout or "{}")
        stream = (data.get("streams") or [{}])[0]
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
    except (ValueError, TypeError, json.JSONDecodeError, IndexError):
        width = height = 0

    if not width or not height:
        return

    resolution = f"{width}x{height}"
    with process_lock:
        entry = active_processes.get(stream_key)
        if entry:
            entry["resolution"] = resolution
    logger.info("Detected resolution for %s: %s", stream_key, resolution)


def stream_status_payload(stream_key: str) -> dict[str, Any]:
    """Build the client-facing stream status payload."""

    user = get_active_user_by_stream_key(stream_key)
    with process_lock:
        publisher = active_publishers.get(stream_key)
        process_entry = active_processes.get(stream_key)
        recent_entry = next(
            (item for item in recent_processes if item.get("stream_key") == stream_key),
            None,
        )

    process = process_snapshot(stream_key, process_entry) if process_entry else None
    recent = recent_entry if recent_entry else None
    platform_statuses = build_platform_statuses(user, publisher, process, recent)
    destinations = 0
    if publisher:
        destinations = int(publisher.get("destinations") or 0)
    elif process:
        destinations = int(process.get("destinations") or 0)

    frame = int(process.get("frame") or 0) if process else 0
    fps = process.get("fps") if process and process.get("fps") is not None else None
    bitrate = process.get("bitrate") if process and process.get("bitrate") else ""
    resolution = process.get("resolution") if process and process.get("resolution") else ""
    dropped_frames = process.get("dropped_frames") if process and process.get("dropped_frames") is not None else None

    if not publisher:
        color = "red"
        label = "Нет входящего потока"
        message = "VideoCoder не публикует поток в SRS или SRS еще не прислал on_publish."
    elif destinations == 0:
        color = "yellow"
        label = "Есть поток - рестрим не запущен"
        message = ""
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
        "fps": fps,
        "bitrate": bitrate,
        "speed": process.get("speed") if process else "",
        "resolution": resolution,
        "dropped_frames": dropped_frames,
        "destinations": destinations,
        "platform_statuses": platform_statuses,
    }


async def stream_status_events(request: Request, stream_key: str) -> Any:
    """Yield stream status as Server-Sent Events until the client disconnects."""

    while not await request.is_disconnected():
        payload = stream_status_payload(stream_key)
        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        await asyncio.sleep(1)


def active_streams() -> dict[str, Any]:
    """Serialize active publishers, FFmpeg workers, and recent exits for admin APIs."""

    with process_lock:
        streams = [process_snapshot(key, entry) for key, entry in active_processes.items()]
        publishers = [{"stream_key": key, **value} for key, value in active_publishers.items()]
        recent = recent_processes[:20]
    return {"code": 0, "streams": streams, "publishers": publishers, "recent": recent}


def stream_logs(stream_key: str, lines: int = 80) -> dict[str, Any]:
    """Return the latest FFmpeg log lines for an active or recent stream."""

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
        log_path = find_latest_log_path_for_stream(stream_key)

    if log_path is None:
        return {"code": 1, "message": "stream log was not found", "lines": []}

    lines_payload = tail_log_file(log_path, lines=lines)
    return {
        "code": 0,
        "stream_key": stream_key,
        "log_path": str(log_path or ""),
        "lines": lines_payload,
        "message": "" if lines_payload else "stream log is empty",
    }


def admin_live_dashboard_payload() -> dict[str, Any]:
    """Build live admin dashboard payload for metrics and stream workers."""

    streams_payload = active_streams()
    return {
        "code": 0,
        "database_backend": DATABASE_BACKEND,
        "metrics": system_metrics_payload(),
        "streams": streams_payload["streams"],
        "publishers": streams_payload["publishers"],
        "recent": streams_payload["recent"],
    }


async def admin_dashboard_events(request: Request) -> Any:
    """Yield admin live dashboard data as Server-Sent Events."""

    while not await request.is_disconnected():
        payload = admin_live_dashboard_payload()
        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        await asyncio.sleep(1)


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


class ExternalProcess:
    """Minimal handle for an FFmpeg worker that outlived the previous backend."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode: int | None = None

    def poll(self) -> int | None:
        if self.returncode is not None:
            return self.returncode
        try:
            os.kill(self.pid, 0)
        except ProcessLookupError:
            self.returncode = -1
            return self.returncode
        except PermissionError:
            return None
        return None

    def wait(self, timeout: float | None = None) -> int:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            code = self.poll()
            if code is not None:
                return code
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(cmd=f"pid:{self.pid}", timeout=timeout or 0)
            time.sleep(0.2)

    def terminate(self) -> None:
        try:
            os.kill(self.pid, signal.SIGTERM)
        except ProcessLookupError:
            self.returncode = -1

    def kill(self) -> None:
        try:
            os.kill(self.pid, signal.SIGKILL)
        except ProcessLookupError:
            self.returncode = -1


def persist_publishers_state() -> None:
    """Write active publishers to disk so restarts can recover live sessions."""

    with process_lock:
        payload = {
            key: {
                "published_at": meta.get("published_at"),
                "destinations": meta.get("destinations", 0),
                "ffmpeg_started": bool(meta.get("ffmpeg_started")),
            }
            for key, meta in active_publishers.items()
        }
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp_path = PUBLISHERS_STATE_PATH.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(PUBLISHERS_STATE_PATH)
    except OSError:
        logger.exception("Failed to persist active publishers state")


def load_publishers_state() -> dict[str, dict[str, Any]]:
    """Load previously persisted publisher sessions from disk."""

    if not PUBLISHERS_STATE_PATH.is_file():
        return {}
    try:
        raw = json.loads(PUBLISHERS_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("Failed to load active publishers state")
        return {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for key, meta in raw.items():
        if isinstance(key, str) and isinstance(meta, dict):
            result[key] = meta
    return result


def forget_publisher(stream_key: str) -> None:
    """Remove a publisher from memory and persisted state."""

    with process_lock:
        active_publishers.pop(stream_key, None)
    persist_publishers_state()


def extract_stream_key_from_input_url(input_url: str) -> str | None:
    """Extract stream key from an SRS input URL built via SRS_INPUT_URL_TEMPLATE."""

    template = SRS_INPUT_URL_TEMPLATE
    if "{stream_key}" not in template:
        return None
    prefix, suffix = template.split("{stream_key}", 1)
    if not input_url.startswith(prefix):
        return None
    remainder = input_url[len(prefix) :]
    if suffix:
        if not remainder.endswith(suffix):
            return None
        remainder = remainder[: -len(suffix)]
    remainder = remainder.strip("/")
    return remainder or None


def extract_stream_key_from_ffmpeg_args(args: list[str]) -> str | None:
    """Return stream key from a restream FFmpeg argv list, if recognizable."""

    for index, arg in enumerate(args):
        if arg == "-i" and index + 1 < len(args):
            return extract_stream_key_from_input_url(args[index + 1])
    return None


def extract_progress_path_from_ffmpeg_args(args: list[str]) -> Path | None:
    """Return -progress path from FFmpeg argv when present."""

    for index, arg in enumerate(args):
        if arg == "-progress" and index + 1 < len(args):
            value = args[index + 1]
            if value and value != "pipe:2":
                return Path(value)
    return None


def count_flv_outputs_in_ffmpeg_args(args: list[str]) -> int:
    """Count FLV outputs in an FFmpeg argv list."""

    return sum(1 for index, arg in enumerate(args) if arg == "-f" and index + 1 < len(args) and args[index + 1] == "flv")


def read_process_cmdline(pid: int) -> list[str] | None:
    """Read NUL-separated cmdline for a Linux process."""

    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None
    if not raw:
        return None
    return [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]


def discover_orphan_ffmpeg_workers() -> list[dict[str, Any]]:
    """Find FFmpeg restream workers started by a previous backend process."""

    ffmpeg_name = Path(FFMPEG_BIN).name
    workers: list[dict[str, Any]] = []
    try:
        proc_entries = list(Path("/proc").iterdir())
    except OSError:
        return []

    for entry in proc_entries:
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        args = read_process_cmdline(pid)
        if not args:
            continue
        binary_name = Path(args[0]).name
        if binary_name != ffmpeg_name and binary_name != "ffmpeg":
            continue
        stream_key = extract_stream_key_from_ffmpeg_args(args)
        if not stream_key:
            continue
        workers.append(
            {
                "pid": pid,
                "stream_key": stream_key,
                "args": args,
                "progress_path": extract_progress_path_from_ffmpeg_args(args),
                "destinations": count_flv_outputs_in_ffmpeg_args(args),
            }
        )
    return workers


def attach_monitor_threads(
    stream_key: str,
    process: Any,
    progress_path: Path,
    input_url: str | None = None,
) -> None:
    """Start monitor/stall(/optional probe) threads for a managed FFmpeg process."""

    threading.Thread(
        target=monitor_process,
        args=(stream_key, process),
        name=f"ffmpeg-monitor-{stream_key}",
        daemon=True,
    ).start()
    threading.Thread(
        target=watch_ffmpeg_stall,
        args=(stream_key, process, progress_path),
        name=f"ffmpeg-stall-{stream_key}",
        daemon=True,
    ).start()
    if input_url:
        threading.Thread(
            target=probe_stream_resolution,
            args=(stream_key, input_url),
            name=f"ffprobe-resolution-{stream_key}",
            daemon=True,
        ).start()


def adopt_ffmpeg_worker(worker: dict[str, Any]) -> bool:
    """Attach monitors to an already-running FFmpeg worker and restore publisher state."""

    stream_key = str(worker["stream_key"])
    pid = int(worker["pid"])
    process = ExternalProcess(pid)
    if process.poll() is not None:
        return False

    log_path = find_latest_log_path_for_stream(stream_key) or log_path_for_stream(stream_key)
    progress_path = worker.get("progress_path") or progress_path_for_log(log_path)
    destinations = int(worker.get("destinations") or 0)
    published_at = utc_now_iso()

    with process_lock:
        active_processes[stream_key] = {
            "process": process,
            "started_at": published_at,
            "destinations": destinations,
            "log_path": log_path,
            "progress_path": progress_path,
            "resolution": "",
            "adopted": True,
        }
        active_publishers[stream_key] = {
            "published_at": published_at,
            "destinations": destinations,
            "ffmpeg_started": True,
            "adopted": True,
        }

    attach_monitor_threads(stream_key, process, Path(progress_path))
    logger.warning("Adopted orphan FFmpeg pid=%s stream=%s", pid, stream_key)
    return True


def kill_process_pid(pid: int) -> None:
    """Best-effort terminate/kill for an orphan PID."""

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        logger.warning("No permission to terminate orphan FFmpeg pid=%s", pid)
        return

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.2)
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        return


def recover_ffmpeg_workers_on_startup() -> dict[str, int]:
    """Adopt or kill orphan FFmpeg workers and restore publisher sessions after restart."""

    stats = {"adopted": 0, "killed": 0, "restarted": 0}
    orphans = discover_orphan_ffmpeg_workers()
    seen_keys: set[str] = set()
    adopt = FFMPEG_ORPHAN_POLICY != "kill"

    for worker in sorted(orphans, key=lambda item: int(item["pid"])):
        stream_key = str(worker["stream_key"])
        pid = int(worker["pid"])
        user = get_active_user_by_stream_key(stream_key)
        duplicate = stream_key in seen_keys
        if not adopt or user is None or duplicate:
            kill_process_pid(pid)
            stats["killed"] += 1
            logger.warning(
                "Killed orphan FFmpeg pid=%s stream=%s (reason=%s)",
                pid,
                stream_key,
                "duplicate" if duplicate else ("policy_kill" if user else "unknown_user"),
            )
            continue
        if adopt_ffmpeg_worker(worker):
            seen_keys.add(stream_key)
            stats["adopted"] += 1
        else:
            stats["killed"] += 1

    persisted = load_publishers_state()
    for stream_key, meta in persisted.items():
        if stream_key in seen_keys:
            continue
        with process_lock:
            already_active = stream_key in active_processes
        if already_active:
            continue
        user = get_active_user_by_stream_key(stream_key)
        if user is None:
            forget_publisher(stream_key)
            continue
        allowed, _message = validate_destination_limit(user)
        destinations = get_enabled_destinations(user)
        if not allowed or not destinations:
            forget_publisher(stream_key)
            continue
        if not srs_input_seems_live(stream_key):
            logger.info(
                "Skip FFmpeg recovery for %s: SRS input is not live",
                stream_key,
            )
            forget_publisher(stream_key)
            continue
        with process_lock:
            active_publishers[stream_key] = {
                "published_at": meta.get("published_at") or utc_now_iso(),
                "destinations": len(destinations),
                "ffmpeg_started": False,
                "recovered": True,
            }
        try:
            if start_ffmpeg(stream_key, destinations):
                stats["restarted"] += 1
                seen_keys.add(stream_key)
                logger.warning("Restarted FFmpeg for persisted publisher %s", stream_key)
            else:
                forget_publisher(stream_key)
        except Exception:
            logger.exception("Failed to restart FFmpeg for persisted publisher %s", stream_key)
            forget_publisher(stream_key)

    persist_publishers_state()
    return stats


def build_ffmpeg_command(
    stream_key: str,
    destinations: list[str],
    progress_path: Path,
) -> list[str]:
    """Build one FFmpeg command that fans out the input to all enabled outputs."""

    input_url = SRS_INPUT_URL_TEMPLATE.format(stream_key=stream_key)
    timeout_us = str(FFMPEG_RW_TIMEOUT_US)
    command = [
        FFMPEG_BIN,
        "-hide_banner",
        "-nostats",
        "-loglevel",
        "warning",
        "-progress",
        str(progress_path),
        "-rw_timeout",
        timeout_us,
        "-i",
        input_url,
        "-c",
        "copy",
    ]
    for destination in destinations:
        # Per-output network timeout so a hung RTMP target can fail the muxer.
        command.extend(["-f", "flv", "-rw_timeout", timeout_us, destination])
    return command


def bump_ffmpeg_generation(stream_key: str) -> int:
    """Invalidate in-flight FFmpeg starts for a stream key and return the new generation."""

    with process_lock:
        next_generation = ffmpeg_worker_generations.get(stream_key, 0) + 1
        ffmpeg_worker_generations[stream_key] = next_generation
        return next_generation


def srs_input_seems_live(stream_key: str) -> bool:
    """Probe whether SRS still has a publishable input for this stream key."""

    input_url = SRS_INPUT_URL_TEMPLATE.format(stream_key=stream_key)
    try:
        result = subprocess.run(
            [
                FFPROBE_BIN,
                "-v",
                "error",
                "-rw_timeout",
                "2000000",
                "-show_entries",
                "format=format_name",
                "-of",
                "csv=p=0",
                input_url,
            ],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0 and bool((result.stdout or "").strip())


def stop_process(stream_key: str, *, clear_restart_state: bool = False) -> bool:
    """Terminate a running FFmpeg worker for a stream key."""

    bump_ffmpeg_generation(stream_key)
    with process_lock:
        entry = active_processes.pop(stream_key, None)

    if entry is None:
        return False

    process: Any = entry["process"]
    if process.poll() is not None:
        logger.info("FFmpeg for %s already exited with code %s", stream_key, process.returncode)
        if clear_restart_state:
            clear_ffmpeg_restart_state(stream_key)
        return True

    logger.info("Stopping FFmpeg for stream %s", stream_key)
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        logger.warning("FFmpeg for %s did not stop gracefully; killing it", stream_key)
        process.kill()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("FFmpeg for %s did not exit after kill", stream_key)

    snapshot = process_snapshot(stream_key, entry)
    snapshot["ended_at"] = utc_now_iso()
    snapshot["stopped_by"] = "admin_or_webhook"
    with process_lock:
        recent_processes.insert(0, snapshot)
        del recent_processes[50:]
    if clear_restart_state:
        clear_ffmpeg_restart_state(stream_key)
    return True


def clear_ffmpeg_restart_state(stream_key: str) -> None:
    """Reset FFmpeg auto-restart counters when a publish session ends."""

    ffmpeg_restart_counts.pop(stream_key, None)


def maybe_restart_ffmpeg(stream_key: str, return_code: int) -> None:
    """Restart FFmpeg while SRS still reports an active publisher."""

    if return_code == 0 or FFMPEG_MAX_RESTARTS <= 0:
        return

    with process_lock:
        still_published = stream_key in active_publishers
        restart_count = ffmpeg_restart_counts.get(stream_key, 0)
        generation_before_sleep = ffmpeg_worker_generations.get(stream_key, 0)

    if not still_published:
        return

    if restart_count >= FFMPEG_MAX_RESTARTS:
        logger.error(
            "FFmpeg for %s crashed %s times; auto-restart limit reached",
            stream_key,
            restart_count,
        )
        return

    user = get_active_user_by_stream_key(stream_key)
    if user is None:
        return

    allowed, limit_message = validate_destination_limit(user)
    if not allowed:
        logger.error(
            "Skip FFmpeg restart for %s because destination limit is invalid: %s",
            stream_key,
            limit_message,
        )
        return

    destinations = get_enabled_destinations(user)
    if not destinations:
        return

    ffmpeg_restart_counts[stream_key] = restart_count + 1
    if FFMPEG_RESTART_DELAY_SECONDS:
        time.sleep(FFMPEG_RESTART_DELAY_SECONDS)

    with process_lock:
        if stream_key not in active_publishers:
            return
        if ffmpeg_worker_generations.get(stream_key, 0) != generation_before_sleep:
            return
        current = active_processes.get(stream_key)
        if current and current.get("process") is not None and current["process"].poll() is None:
            # A newer healthy worker already replaced the crashed one.
            return

    logger.warning(
        "Restarting FFmpeg for %s after exit code %s (attempt %s/%s)",
        stream_key,
        return_code,
        restart_count + 1,
        FFMPEG_MAX_RESTARTS,
    )
    try:
        start_ffmpeg(stream_key, destinations)
    except Exception:
        logger.exception("Failed to auto-restart FFmpeg for %s", stream_key)


def monitor_process(stream_key: str, process: Any) -> None:
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
        maybe_restart_ffmpeg(stream_key, return_code)


def watch_ffmpeg_stall(
    stream_key: str,
    process: Any,
    progress_path: Path,
) -> None:
    """Kill FFmpeg when -progress stops advancing for too long."""

    if FFMPEG_STALL_TIMEOUT_SECONDS <= 0:
        return

    if FFMPEG_STALL_GRACE_SECONDS:
        time.sleep(FFMPEG_STALL_GRACE_SECONDS)

    last_marker = ""
    last_change = time.monotonic()

    while process.poll() is None:
        with process_lock:
            current = active_processes.get(stream_key)
            still_ours = bool(current and current.get("process") is process)
            still_published = stream_key in active_publishers
        if not still_ours or not still_published:
            return

        marker = ""
        try:
            if progress_path.is_file():
                metrics = parse_ffmpeg_progress(progress_path)
                marker = str(metrics.get("out_time_ms") or metrics.get("frame") or "")
                if not marker:
                    marker = str(progress_path.stat().st_mtime_ns)
        except OSError:
            marker = ""

        now = time.monotonic()
        if marker and marker != last_marker:
            last_marker = marker
            last_change = now
        elif now - last_change >= FFMPEG_STALL_TIMEOUT_SECONDS:
            logger.error(
                "FFmpeg for %s stalled for %ss (no progress); killing hung process",
                stream_key,
                int(FFMPEG_STALL_TIMEOUT_SECONDS),
            )
            try:
                process.kill()
            except OSError:
                logger.exception("Failed to kill stalled FFmpeg for %s", stream_key)
            return

        time.sleep(5)


def start_ffmpeg(stream_key: str, destinations: list[str]) -> bool:
    """Start or replace a FFmpeg worker for the stream key."""

    if not destinations:
        logger.info("Stream %s accepted without restream destinations", stream_key)
        return False

    # SRS may retry callbacks; ensure only one worker exists per stream key.
    stop_process(stream_key)
    generation = bump_ffmpeg_generation(stream_key)

    input_url = SRS_INPUT_URL_TEMPLATE.format(stream_key=stream_key)
    log_path = log_path_for_stream(stream_key)
    progress_path = progress_path_for_log(log_path)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    progress_path.write_text("", encoding="utf-8")

    command = build_ffmpeg_command(stream_key, destinations, progress_path)
    redacted_command = command[:]
    for index, value in enumerate(redacted_command):
        if value.startswith(("rtmp://", "rtmps://")) and index > 0:
            redacted_command[index] = "[RTMP_OUTPUT_REDACTED]"
    logger.info("Starting FFmpeg for %s: %s", stream_key, " ".join(redacted_command))

    with log_path.open("ab") as log_file:
        log_file.write(f"[{utc_now_iso()}] Starting: {' '.join(redacted_command)}\n".encode("utf-8"))
        log_file.flush()
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=log_file,
            start_new_session=True,
        )

    with process_lock:
        if ffmpeg_worker_generations.get(stream_key) != generation:
            logger.warning("Discarding superseded FFmpeg start for %s pid=%s", stream_key, process.pid)
            try:
                process.kill()
            except OSError:
                pass
            return False
        active_processes[stream_key] = {
            "process": process,
            "started_at": utc_now_iso(),
            "destinations": len(destinations),
            "log_path": log_path,
            "progress_path": progress_path,
            "resolution": "",
            "generation": generation,
        }
        publisher = active_publishers.get(stream_key)
        if publisher is not None:
            publisher["ffmpeg_started"] = True
            publisher["destinations"] = len(destinations)

    attach_monitor_threads(stream_key, process, progress_path, input_url=input_url)
    persist_publishers_state()
    return True


def sync_live_restream_worker(user: dict[str, Any]) -> bool:
    """Apply updated destination settings to a currently published stream."""

    stream_key = str(user.get("stream_key") or "")
    if not stream_key:
        return False

    with process_lock:
        publisher = active_publishers.get(stream_key)

    if publisher is None:
        return False

    destinations = get_enabled_destinations(user)
    if destinations:
        started = start_ffmpeg(stream_key, destinations)
    else:
        stop_process(stream_key)
        started = False

    with process_lock:
        current_publisher = active_publishers.get(stream_key)
        if current_publisher is not None:
            current_publisher["destinations"] = len(destinations)
            current_publisher["ffmpeg_started"] = started
    persist_publishers_state()
    return started


@app.post("/api/auth/login")
def api_login(payload: LoginPayload, response: Response) -> dict[str, Any]:
    """Authenticate a user for the future web frontend."""

    user = authenticate_user(payload.username, payload.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid credentials or blocked account")
    return create_token_response(user, response)


@app.post("/api/auth/register")
def api_register(payload: RegisterPayload, response: Response) -> dict[str, Any]:
    """Register a client user and return an API session token."""

    success, message, user = create_user(payload.username, payload.password, payload.email)
    if not success or user is None:
        raise HTTPException(status_code=400, detail=message)
    return create_token_response(user, response)


@app.post("/api/auth/logout")
def api_logout(
    response: Response,
    authorization: str | None = Header(default=None),
    session_cookie: str | None = Cookie(default=None, alias=COOKIE_NAME),
) -> dict[str, Any]:
    """Delete the current API session token."""

    cookie_token = session_cookie if isinstance(session_cookie, str) else None
    token = cookie_token or extract_bearer_token(authorization)
    if token:
        delete_auth_session(token)
    clear_session_cookie(response)
    return {"code": 0, "message": "Logged out"}


@app.post("/api/auth/forgot-password")
def api_forgot_password(payload: ForgotPasswordPayload) -> dict[str, Any]:
    """Request password reset without revealing whether the account exists."""

    generic_message = (
        "Если аккаунт существует и для него настроен email, "
        "мы отправили инструкции по восстановлению пароля."
    )
    user, token = create_password_reset_token(payload.identifier)
    if user is None or token is None:
        return {"code": 0, "message": generic_message, "delivery": "none"}

    result = send_password_reset_email(user, token)
    return {
        "code": 0,
        "message": result.get("message") or generic_message,
        "delivery": result.get("delivery") or "none",
    }


@app.post("/api/auth/reset-password")
def api_reset_password(payload: PasswordResetPayload) -> dict[str, Any]:
    """Reset password using a valid email reset token."""

    success, message = reset_password_with_token(payload.token, payload.new_password)
    if not success:
        raise HTTPException(status_code=400, detail=message)
    return {"code": 0, "message": message}


@app.get("/api/me")
def api_me(user: dict[str, Any] = Depends(get_current_api_user)) -> dict[str, Any]:
    """Return the current authenticated user."""

    return {"code": 0, "user": public_user(user)}


@app.get("/api/me/settings")
def api_my_settings(user: dict[str, Any] = Depends(get_current_api_user)) -> dict[str, Any]:
    """Return current user's restream settings."""

    fresh_user = get_user_by_id(int(user["id"]))
    return {"code": 0, "user": public_user(fresh_user)}


@app.put("/api/me/settings")
def api_update_my_settings(
    payload: RestreamSettingsPayload,
    user: dict[str, Any] = Depends(get_current_api_user),
) -> dict[str, Any]:
    """Update current user's restream settings."""

    settings = model_to_dict(payload, exclude_unset=True)
    stream_title = settings.pop("stream_title", None)

    if settings:
        pending_user = build_pending_user_settings(user, settings)
        allowed, message = validate_destination_limit(pending_user)
        if not allowed:
            raise HTTPException(status_code=400, detail=message)
        urls_ok, urls_message = validate_destination_urls(pending_user)
        if not urls_ok:
            raise HTTPException(status_code=400, detail=urls_message)
        update_restream_settings(int(user["id"]), settings)

    if stream_title is not None:
        success, message = update_stream_title(int(user["id"]), stream_title)
        if not success:
            raise HTTPException(status_code=400, detail=message)

    fresh_user = get_user_by_id(int(user["id"]))
    ffmpeg_started = sync_live_restream_worker(fresh_user) if fresh_user and settings else False
    return {"code": 0, "user": public_user(fresh_user), "ffmpeg_started": ffmpeg_started}


@app.post("/api/me/settings")
@app.patch("/api/me/settings")
def api_update_my_settings_title(
    payload: StreamTitlePayload,
    user: dict[str, Any] = Depends(get_current_api_user),
) -> dict[str, Any]:
    """Update broadcast title through the existing settings URL."""

    success, message = update_stream_title(int(user["id"]), payload.stream_title)
    if not success:
        raise HTTPException(status_code=400, detail=message)
    return {"code": 0, "message": message, "user": public_user(get_user_by_id(int(user["id"])))}


@app.post("/api/me/password")
def api_change_my_password(
    payload: PasswordChangePayload,
    user: dict[str, Any] = Depends(get_current_api_user),
) -> dict[str, Any]:
    """Change current user's password."""

    success, message = change_user_password(
        int(user["id"]),
        payload.current_password,
        payload.new_password,
    )
    if not success:
        raise HTTPException(status_code=400, detail=message)
    return {"code": 0, "message": message}


@app.post("/api/me/stream-title")
@app.put("/api/me/stream-title")
def api_update_my_stream_title(
    payload: StreamTitlePayload,
    user: dict[str, Any] = Depends(get_current_api_user),
) -> dict[str, Any]:
    """Update current user's broadcast title."""

    success, message = update_stream_title(int(user["id"]), payload.stream_title)
    if not success:
        raise HTTPException(status_code=400, detail=message)
    return {"code": 0, "message": message, "user": public_user(get_user_by_id(int(user["id"])))}


@app.post("/api/me/stream-key")
@app.put("/api/me/stream-key")
def api_reset_my_stream_key(user: dict[str, Any] = Depends(get_current_api_user)) -> dict[str, Any]:
    """Regenerate current user's stream key and stop old active worker."""

    stop_process(user["stream_key"], clear_restart_state=True)
    forget_publisher(user["stream_key"])
    success, message, stream_key = regenerate_user_stream_key(int(user["id"]))
    if not success:
        raise HTTPException(status_code=400, detail=message)
    fresh_user = get_user_by_id(int(user["id"]))
    if fresh_user is None or fresh_user.get("stream_key") != stream_key:
        raise HTTPException(status_code=500, detail="Stream key was generated but account refresh failed")
    return {
        "code": 0,
        "message": message,
        "stream_key": stream_key,
        "user": public_user(fresh_user),
    }


@app.get("/api/me/stream-status")
def api_my_stream_status(user: dict[str, Any] = Depends(get_current_api_user)) -> dict[str, Any]:
    """Return current user's stream status."""

    return stream_status_payload(user["stream_key"])


@app.get("/api/me/stream-status/events")
def api_my_stream_status_events(
    request: Request,
    user: dict[str, Any] = Depends(get_current_api_user),
) -> StreamingResponse:
    """Stream current user's status via Server-Sent Events."""

    return StreamingResponse(
        stream_status_events(request, user["stream_key"]),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/me/stream-logs")
def api_my_stream_logs(
    lines: int = 80,
    user: dict[str, Any] = Depends(get_current_api_user),
) -> dict[str, Any]:
    """Return latest FFmpeg log lines for the current user's stream."""

    response = stream_logs(user["stream_key"], lines=lines)
    if response.get("code") == 0 and response.get("lines"):
        return response

    username_log_path = find_latest_log_path_for_username(str(user.get("username") or ""))
    if username_log_path is None:
        return response

    lines_payload = tail_log_file(username_log_path, lines=lines)
    return {
        "code": 0 if lines_payload else 1,
        "stream_key": user["stream_key"],
        "log_path": str(username_log_path),
        "lines": lines_payload,
        "message": "" if lines_payload else "stream log is empty",
    }


@app.get("/api/admin/dashboard/events")
def api_admin_dashboard_events(
    request: Request,
    _admin: dict[str, Any] = Depends(get_current_admin),
) -> StreamingResponse:
    """Stream admin metrics and stream workers via Server-Sent Events."""

    return StreamingResponse(
        admin_dashboard_events(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/admin/users")
def api_admin_users(_admin: dict[str, Any] = Depends(get_current_admin)) -> dict[str, Any]:
    """Return all users for a future admin frontend."""

    return {"code": 0, "users": list_users()}


@app.patch("/api/admin/users/{user_id}/active")
def api_admin_set_user_active(
    user_id: int,
    payload: ActivePayload,
    _admin: dict[str, Any] = Depends(get_current_admin),
) -> dict[str, Any]:
    """Block or unblock a user account."""

    target = get_user_by_id(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    if not payload.is_active:
        stop_process(target["stream_key"], clear_restart_state=True)
        forget_publisher(target["stream_key"])
    set_user_active(user_id, payload.is_active)
    return {"code": 0, "user": public_user(get_user_by_id(user_id))}


@app.post("/api/admin/users/{user_id}/password")
def api_admin_set_user_password(
    user_id: int,
    payload: AdminPasswordPayload,
    _admin: dict[str, Any] = Depends(get_current_admin),
) -> dict[str, Any]:
    """Reset a user password as admin."""

    success, message = update_user_password(user_id, payload.new_password)
    if not success:
        raise HTTPException(status_code=400, detail=message)
    return {"code": 0, "message": message}


@app.patch("/api/admin/users/{user_id}/plan")
def api_admin_set_user_plan(
    user_id: int,
    payload: PlanPayload,
    _admin: dict[str, Any] = Depends(get_current_admin),
) -> dict[str, Any]:
    """Update a user's plan and destination limit as admin."""

    success, message = update_user_plan(user_id, payload.plan, payload.max_destinations)
    if not success:
        raise HTTPException(status_code=400, detail=message)
    fresh_user = get_user_by_id(user_id)
    if fresh_user is not None:
        allowed, limit_message = validate_destination_limit(fresh_user)
        if not allowed:
            # Downgrade must stop an over-limit live restream until the client trims destinations.
            stop_process(str(fresh_user.get("stream_key") or ""), clear_restart_state=True)
            forget_publisher(str(fresh_user.get("stream_key") or ""))
            message = f"{message} {limit_message} Активный рестрим остановлен."
        else:
            sync_live_restream_worker(fresh_user)
    return {"code": 0, "message": message, "user": public_user(fresh_user)}


@app.post("/api/admin/users/{user_id}/stream-key")
def api_admin_reset_stream_key(
    user_id: int,
    _admin: dict[str, Any] = Depends(get_current_admin),
) -> dict[str, Any]:
    """Regenerate a user's stream key as admin."""

    target = get_user_by_id(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    stop_process(target["stream_key"], clear_restart_state=True)
    forget_publisher(target["stream_key"])
    success, message, stream_key = regenerate_user_stream_key(user_id)
    if not success:
        raise HTTPException(status_code=400, detail=message)
    return {"code": 0, "message": message, "stream_key": stream_key}


@app.get("/api/admin/streams")
def api_admin_streams(_admin: dict[str, Any] = Depends(get_current_admin)) -> dict[str, Any]:
    """Return active publishers, FFmpeg workers, and recent exits."""

    return active_streams()


@app.post("/api/admin/streams/{stream_key}/stop")
def api_admin_stop_stream(
    stream_key: str,
    _admin: dict[str, Any] = Depends(get_current_admin),
) -> dict[str, Any]:
    """Stop an active FFmpeg worker as admin."""

    forget_publisher(stream_key)
    stopped = stop_process(stream_key, clear_restart_state=True)
    return {"code": 0, "stream_key": stream_key, "stopped": stopped}


@app.get("/api/admin/streams/{stream_key}/logs")
def api_admin_stream_logs(
    stream_key: str,
    lines: int = 80,
    _admin: dict[str, Any] = Depends(get_current_admin),
) -> dict[str, Any]:
    """Return latest FFmpeg log lines for an active or recent stream."""

    return stream_logs(stream_key, lines=lines)


@app.get("/api/admin/system-metrics")
def api_admin_system_metrics(_admin: dict[str, Any] = Depends(get_current_admin)) -> dict[str, Any]:
    """Return server metrics for a future admin frontend."""

    return system_metrics_payload()


@app.get("/api/admin/backups")
def api_admin_backups(_admin: dict[str, Any] = Depends(get_current_admin)) -> dict[str, Any]:
    """Return recent SQLite backups."""

    return {"code": 0, "backups": list_database_backups()}


@app.post("/api/admin/backups")
def api_admin_create_backup(_admin: dict[str, Any] = Depends(get_current_admin)) -> dict[str, Any]:
    """Create a SQLite backup."""

    return {"code": 0, "backup": create_database_backup()}


@app.get("/health")
def health() -> dict[str, Any]:
    """Simple readiness endpoint."""

    db_ok, db_detail = check_database()
    with process_lock:
        active_count = len(active_processes)
    status = "ok" if db_ok else "degraded"
    return {
        "status": status,
        "active_streams": active_count,
        "database": {
            "ok": db_ok,
            "backend": DATABASE_BACKEND,
            "detail": db_detail,
            "path": str(DATABASE_PATH),
        },
    }


@app.post("/on_publish")
async def on_publish(request: Request) -> JSONResponse:
    """Authorize SRS publishing and start FFmpeg fan-out for enabled platforms."""

    verify_srs_webhook(request)
    payload = await parse_srs_payload(request)
    stream_key = extract_stream_key(payload)
    if not stream_key:
        return srs_error("stream key is required", status_code=400)

    user = get_active_user_by_stream_key(stream_key)
    if user is None:
        logger.warning("Rejected publish for inactive or unknown stream key: %s", stream_key)
        return srs_error("stream is not allowed")

    destinations = get_enabled_destinations(user)
    allowed, limit_message = validate_destination_limit(user)
    if not allowed:
        logger.warning("Rejected publish for %s: %s", stream_key, limit_message)
        return srs_error(limit_message)

    published_at = utc_now_iso()
    clear_ffmpeg_restart_state(stream_key)
    with process_lock:
        active_publishers[stream_key] = {
            "published_at": published_at,
            "destinations": len(destinations),
            "ffmpeg_started": False,
        }
    persist_publishers_state()

    try:
        started = start_ffmpeg(stream_key, destinations)
    except FileNotFoundError:
        logger.exception("FFmpeg binary was not found")
        forget_publisher(stream_key)
        return srs_error("ffmpeg is not installed", status_code=500)
    except Exception as exc:
        logger.exception("Failed to start FFmpeg for %s", stream_key)
        forget_publisher(stream_key)
        return srs_error(f"failed to start restream: {exc}", status_code=500)

    with process_lock:
        publisher = active_publishers.get(stream_key)
        if publisher is not None:
            publisher["ffmpeg_started"] = started
    persist_publishers_state()

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

    verify_srs_webhook(request)
    payload = await parse_srs_payload(request)
    stream_key = extract_stream_key(payload)
    if not stream_key:
        return srs_error("stream key is required", status_code=400)

    forget_publisher(stream_key)
    stopped = stop_process(stream_key, clear_restart_state=True)
    return JSONResponse(
        status_code=200,
        content={"code": 0, "stream_key": stream_key, "stopped": stopped},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
