"""FastAPI backend for SRS webhooks and FFmpeg restream workers.

Run locally (bind loopback in production; put nginx in front):
    uvicorn main:app --host 127.0.0.1 --port 8000

SRS http_hooks cannot set custom headers, so the shared secret is passed as
a query token when rendering deploy/srs/srs.conf from the template:

    POST http://127.0.0.1:8000/on_publish?token=<RESTREAM_SRS_WEBHOOK_SECRET>
    POST http://127.0.0.1:8000/on_unpublish?token=<RESTREAM_SRS_WEBHOOK_SECRET>

Optional reverse-proxy deployments may instead inject
X-Restream-Webhook-Secret and omit the query token.
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
    apply_destination_profile,
    authenticate_user,
    change_user_password,
    check_database,
    create_auth_session,
    create_database_backup,
    create_destination_profile,
    create_password_reset_token,
    create_user,
    delete_auth_session,
    delete_destination_profile,
    get_active_user_by_stream_key,
    get_enabled_destination_specs,
    get_notification_settings,
    get_user_by_id,
    get_user_by_session_token,
    get_user_by_stream_key,
    get_user_telegram_chat_id,
    init_db,
    list_database_backups,
    list_destination_profiles,
    list_users,
    mask_secret_value,
    regenerate_user_stream_key,
    reset_password_with_token,
    set_user_active,
    update_destination_profile,
    update_notification_settings,
    update_user_plan,
    update_stream_title,
    update_restream_settings,
    update_user_password,
    validate_destination_limit,
    validate_destination_urls,
)
from telegram_notify import (
    DestinationNotifyTracker,
    NotificationGate,
    build_critical_worker_error_message,
    build_destination_error_message,
    build_destination_reconnecting_message,
    build_destination_recovered_message,
    build_stream_started_message,
    build_stream_stopped_message,
    local_time_hhmmss,
    notification_public_status,
    send_telegram_message,
    telegram_bot_configured,
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
    """Validate secrets, run schema migrations, and recover orphan FFmpeg workers."""

    validate_runtime_secrets()

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
FFMPEG_RESTART_WINDOW_SECONDS = max(30.0, float(os.getenv("RESTREAM_FFMPEG_RESTART_WINDOW_SECONDS", "300")))
FFMPEG_RESTART_BACKOFF_BASE_SECONDS = max(1.0, float(os.getenv("RESTREAM_FFMPEG_RESTART_BACKOFF_BASE_SECONDS", "2")))
FFMPEG_RESTART_BACKOFF_MAX_SECONDS = max(1.0, float(os.getenv("RESTREAM_FFMPEG_RESTART_BACKOFF_MAX_SECONDS", "60")))
FFMPEG_STABLE_RESET_SECONDS = max(10.0, float(os.getenv("RESTREAM_FFMPEG_STABLE_RESET_SECONDS", "60")))
# Legacy fixed delay kept as minimum sleep floor.
FFMPEG_RESTART_DELAY_SECONDS = max(0.0, float(os.getenv("RESTREAM_FFMPEG_RESTART_DELAY_SECONDS", "2")))
# Network IO timeout for RTMP read/write (microseconds). Default: 15s.
FFMPEG_RW_TIMEOUT_US = max(1_000_000, int(os.getenv("RESTREAM_FFMPEG_RW_TIMEOUT_US", "15000000")))
# Kill FFmpeg only after prolonged frozen progress. 0 disables stall watchdog.
FFMPEG_STALL_TIMEOUT_SECONDS = max(0.0, float(os.getenv("RESTREAM_FFMPEG_STALL_TIMEOUT_SECONDS", "120")))
FFMPEG_STALL_GRACE_SECONDS = max(0.0, float(os.getenv("RESTREAM_FFMPEG_STALL_GRACE_SECONDS", "60")))
FFMPEG_STALL_POLL_SECONDS = max(1.0, float(os.getenv("RESTREAM_FFMPEG_STALL_POLL_SECONDS", "5")))
FFMPEG_STALL_MIN_SAMPLES = max(2, int(os.getenv("RESTREAM_FFMPEG_STALL_MIN_SAMPLES", "3")))
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
AUTH_TRUSTED_PROXIES = {
    ip.strip()
    for ip in os.getenv("RESTREAM_AUTH_TRUSTED_PROXIES", "127.0.0.1,::1").split(",")
    if ip.strip()
}
AUTH_RATE_LIMIT = max(1, int(os.getenv("RESTREAM_AUTH_RATE_LIMIT", "20")))
AUTH_RATE_WINDOW_SECONDS = max(1, int(os.getenv("RESTREAM_AUTH_RATE_WINDOW_SECONDS", "60")))
ALLOW_INSECURE_DEFAULTS = os.getenv("RESTREAM_ALLOW_INSECURE_DEFAULTS", "false").lower() == "true"
FORBIDDEN_SECRET_MARKERS = ("change_me", "changeme", "password", "secret123")

active_processes: dict[str, dict[str, Any]] = {}
active_publishers: dict[str, dict[str, Any]] = {}
recent_processes: list[dict[str, Any]] = []
ffmpeg_restart_events: dict[str, list[float]] = {}
ffmpeg_worker_generations: dict[str, int] = {}
# Per-destination operator telemetry (never contains stream keys / RTMP secrets).
ffmpeg_worker_telemetry: dict[str, dict[str, Any]] = {}
# In-flight auto-restart countdowns keyed by worker_key.
ffmpeg_pending_restarts: dict[str, dict[str, Any]] = {}
process_lock = threading.Lock()
auth_rate_lock = threading.Lock()
auth_rate_buckets: dict[str, list[float]] = {}


def validate_runtime_secrets() -> None:
    """Fail startup when required production secrets are missing or weak."""

    secret = SRS_WEBHOOK_SECRET
    weak = (not secret) or any(marker in secret.lower() for marker in FORBIDDEN_SECRET_MARKERS)
    if weak:
        message = (
            "RESTREAM_SRS_WEBHOOK_SECRET is missing or uses an insecure default. "
            "Set a strong unique secret shared with SRS (render via deploy/scripts/render_srs_conf.sh). "
            "For local development only, set RESTREAM_ALLOW_INSECURE_DEFAULTS=true."
        )
        if ALLOW_INSECURE_DEFAULTS:
            logger.warning(message)
            return
        raise RuntimeError(message)


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
    yt_profile_id: int | None = None
    vk_active: bool | None = None
    vk_url: str | None = None
    vk_key: str | None = None
    vk_profile_id: int | None = None
    rt_active: bool | None = None
    rt_url: str | None = None
    rt_key: str | None = None
    rt_profile_id: int | None = None
    tg_active: bool | None = None
    tg_url: str | None = None
    tg_key: str | None = None
    tg_profile_id: int | None = None
    custom_active: bool | None = None
    custom_url: str | None = None
    custom_key: str | None = None
    custom_profile_id: int | None = None
    stream_title: str | None = None


class DestinationProfileCreatePayload(BaseModel):
    name: str
    platform_id: str
    base_url: str = ""
    stream_key: str = ""


class DestinationProfileUpdatePayload(BaseModel):
    name: str | None = None
    base_url: str | None = None
    stream_key: str | None = None


class DestinationProfileApplyPayload(BaseModel):
    activate: bool = False


class NotificationSettingsPayload(BaseModel):
    telegram_enabled: bool | None = None
    telegram_username: str | None = None
    telegram_chat_id: str | None = None


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
    payload = {key: value for key, value in user.items() if key != "password"}
    # Never echo raw Telegram chat id; username (public login) is OK to show.
    chat_id = str(payload.pop("notify_tg_chat_id", "") or "")
    username = str(payload.pop("notify_tg_username", "") or "").strip().lstrip("@")
    payload["notify_tg_enabled"] = bool(payload.get("notify_tg_enabled"))
    payload["notify_tg_username"] = f"@{username}" if username else ""
    payload["notify_tg_chat_id_masked"] = mask_secret_value(chat_id)
    payload["notify_tg_chat_id_set"] = bool(chat_id.strip())
    payload["telegram_bot_configured"] = telegram_bot_configured()
    return payload


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
    """Clear the browser session cookie using the same attributes as set."""

    response.delete_cookie(
        key=COOKIE_NAME,
        domain=COOKIE_DOMAIN,
        path="/",
        secure=COOKIE_SECURE,
        httponly=True,
        samesite="lax",
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
    """Return the direct peer IP (never trust X-Forwarded-For for webhook auth)."""

    return request.client.host if request.client else ""


def get_auth_client_ip(request: Request) -> str:
    """Return client IP for auth rate limits; trust XFF only from local proxies."""

    peer = get_request_client_ip(request)
    if peer in AUTH_TRUSTED_PROXIES:
        forwarded = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        if forwarded:
            return forwarded
    return peer or "unknown"


def enforce_auth_rate_limit(request: Request) -> None:
    """Basic in-memory rate limit for public auth endpoints."""

    client_ip = get_auth_client_ip(request)
    bucket_key = f"{client_ip}:{request.url.path}"
    now = time.time()
    window_start = now - AUTH_RATE_WINDOW_SECONDS

    with auth_rate_lock:
        stamps = [stamp for stamp in auth_rate_buckets.get(bucket_key, []) if stamp >= window_start]
        if len(stamps) >= AUTH_RATE_LIMIT:
            auth_rate_buckets[bucket_key] = stamps
            raise HTTPException(
                status_code=429,
                detail="Слишком много попыток. Подождите немного и попробуйте снова.",
            )
        stamps.append(now)
        auth_rate_buckets[bucket_key] = stamps


def verify_srs_webhook(request: Request) -> None:
    """Require SRS webhook secret on every request (trusted IP is never a bypass)."""

    client_ip = get_request_client_ip(request)
    if not SRS_WEBHOOK_SECRET:
        logger.error("Rejected SRS webhook: RESTREAM_SRS_WEBHOOK_SECRET is not configured")
        raise HTTPException(status_code=503, detail="SRS webhook secret is not configured")

    provided = request.headers.get("X-Restream-Webhook-Secret", "")
    if not provided:
        provided = request.query_params.get("token", "")
    if not provided or not secrets.compare_digest(provided, SRS_WEBHOOK_SECRET):
        logger.warning("Rejected SRS webhook from %s: invalid secret", client_ip)
        raise HTTPException(status_code=403, detail="SRS webhook is not allowed")

    if SRS_TRUSTED_IPS and client_ip not in SRS_TRUSTED_IPS:
        logger.info("Accepted SRS webhook by secret from non-listed IP %s", client_ip)


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


def worker_key_for(stream_key: str, platform_id: str) -> str:
    """Return the in-memory key for one destination worker."""

    return f"{stream_key}::{platform_id}"


def split_worker_key(worker_key: str) -> tuple[str, str]:
    """Split a worker key into stream key and platform id."""

    stream_key, separator, platform_id = worker_key.partition("::")
    return stream_key, platform_id if separator else ""


def worker_key_matches_stream(worker_key: str, stream_key: str) -> bool:
    """Return whether a worker key belongs to a stream key."""

    return worker_key == stream_key or worker_key.startswith(f"{stream_key}::")


def platform_title(platform_id: str) -> str:
    """Return a display title for a platform id."""

    for config in PLATFORM_STATUS_CONFIGS:
        if config["id"] == platform_id:
            return str(config["title"])
    return platform_id or "Unknown"


def log_path_for_stream(stream_key: str) -> Path:
    """Return the FFmpeg log path for a stream."""

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return LOG_DIR / f"ffmpeg_{safe_log_name(stream_key)}_{timestamp}.log"


def log_path_for_worker(stream_key: str, platform_id: str) -> Path:
    """Return the FFmpeg log path for one destination worker."""

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return LOG_DIR / f"ffmpeg_{safe_log_name(stream_key)}_{safe_log_name(platform_id)}_{timestamp}.log"


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


def find_latest_log_path_for_worker(stream_key: str, platform_id: str) -> Path | None:
    """Find the newest FFmpeg log for one stream_key + destination platform."""

    safe_key = safe_log_name(stream_key)
    safe_platform = safe_log_name(platform_id)
    if not LOG_DIR.exists() or not LOG_DIR.is_dir():
        return None

    candidates = [
        path
        for path in LOG_DIR.glob(f"ffmpeg_{safe_key}_{safe_platform}_*.log")
        if path.is_file() and not path.name.endswith(".progress")
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

    # Match live_{username}_{12hex}_{platform}_{timestamp}.log (and legacy without platform).
    # Require the username segment so "a" cannot match "alice".
    name_re = re.compile(
        rf"^ffmpeg_live_{re.escape(safe_username)}_[a-f0-9]{{12}}"
        rf"(?:_[a-z0-9]+)?_\d{{8}}_\d{{6}}\.log$"
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


def parse_bitrate_kbps(bitrate: str | None) -> float | None:
    """Parse FFmpeg bitrate strings into kbps. Return None when unknown (never invent 0)."""

    text = (bitrate or "").strip().lower()
    if not text or text in {"n/a", "na", "unknown", "null"}:
        return None
    match = re.match(r"^([0-9]+(?:\.[0-9]+)?)\s*([kmg]?bits?/s|[kmg]?b/?s)?$", text)
    if not match:
        return None
    value = float(match.group(1))
    unit = (match.group(2) or "kbits/s").replace("/", "")
    if unit.startswith("g"):
        return value * 1_000_000
    if unit.startswith("m"):
        return value * 1_000
    if unit.startswith("k"):
        return value
    return value / 1000.0


def parse_resolution_wh(resolution: str | None) -> tuple[int | None, int | None]:
    """Parse '1920x1080' into width/height."""

    text = (resolution or "").strip().lower()
    match = re.match(r"^([0-9]{2,5})\s*[x×]\s*([0-9]{2,5})$", text)
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def uptime_seconds_from_iso(started_at: str | None) -> int | None:
    """Return whole seconds since started_at, or None when unavailable."""

    if not started_at:
        return None
    try:
        started = datetime.fromisoformat(started_at)
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        return max(0, int((datetime.now(timezone.utc) - started).total_seconds()))
    except ValueError:
        return None


def worker_telemetry(worker_key: str) -> dict[str, Any]:
    """Return mutable telemetry dict for one destination worker."""

    telemetry = ffmpeg_worker_telemetry.get(worker_key)
    if telemetry is None:
        telemetry = {
            "restart_count": 0,
            "reconnect_count": 0,
            "last_error": None,
            "last_error_at": None,
            "session_started_at": None,
        }
        ffmpeg_worker_telemetry[worker_key] = telemetry
    return telemetry


def record_worker_error(worker_key: str, error: str) -> None:
    """Store last error for one destination without touching other workers."""

    telemetry = worker_telemetry(worker_key)
    telemetry["last_error"] = (error or "unknown error")[:300]
    telemetry["last_error_at"] = utc_now_iso()


notify_gate = NotificationGate()
notify_tracker = DestinationNotifyTracker()


def _platform_label_for_worker(worker_key: str) -> str:
    _stream_key, platform_id = split_worker_key(worker_key)
    with process_lock:
        entry = active_processes.get(worker_key) or {}
        title = str(entry.get("platform_title") or "")
    return title or platform_title(platform_id)


def _send_user_telegram(user_id: int, text: str) -> bool:
    """Deliver a Telegram message for an enabled user; never logs secrets."""

    chat_id = get_user_telegram_chat_id(user_id)
    if not chat_id:
        return False
    if not telegram_bot_configured():
        return False
    ok, _message = send_telegram_message(chat_id, text)
    return ok


def notify_stream_event(stream_key: str, event: str) -> None:
    """Notify stream started/stopped at most once per cooldown window."""

    user = get_user_by_stream_key(stream_key)
    if user is None:
        return
    user_id = int(user["id"])
    gate_key = f"stream:{user_id}:{stream_key}:{event}"
    if not notify_gate.allow(gate_key):
        return
    username = str(user.get("username") or stream_key)
    when = local_time_hhmmss()
    if event == "started":
        text = build_stream_started_message(username, when=when)
    elif event == "stopped":
        text = build_stream_stopped_message(username, when=when)
    else:
        return
    _send_user_telegram(user_id, text)


def notify_destination_error(
    worker_key: str,
    error: str,
    *,
    restart_in_seconds: int | None = None,
) -> None:
    """Send one ERROR notification per destination outage (no watchdog spam)."""

    if not notify_tracker.begin_error(worker_key):
        return
    stream_key, _platform_id = split_worker_key(worker_key)
    user = get_user_by_stream_key(stream_key)
    if user is None:
        return
    text = build_destination_error_message(
        _platform_label_for_worker(worker_key),
        error=error,
        restart_in_seconds=restart_in_seconds,
        when=local_time_hhmmss(),
    )
    _send_user_telegram(int(user["id"]), text)


def notify_destination_reconnecting(worker_key: str, restart_in_seconds: int | None = None) -> None:
    """Send at most one RECONNECTING notification per destination outage."""

    if not notify_tracker.begin_reconnecting(worker_key):
        return
    stream_key, _platform_id = split_worker_key(worker_key)
    user = get_user_by_stream_key(stream_key)
    if user is None:
        return
    text = build_destination_reconnecting_message(
        _platform_label_for_worker(worker_key),
        restart_in_seconds=restart_in_seconds,
        when=local_time_hhmmss(),
    )
    _send_user_telegram(int(user["id"]), text)


def notify_destination_critical(worker_key: str, error: str) -> None:
    """Send at most one CRITICAL notification per destination outage."""

    if not notify_tracker.begin_critical(worker_key):
        return
    stream_key, _platform_id = split_worker_key(worker_key)
    user = get_user_by_stream_key(stream_key)
    if user is None:
        return
    text = build_critical_worker_error_message(
        _platform_label_for_worker(worker_key),
        error=error,
        when=local_time_hhmmss(),
    )
    _send_user_telegram(int(user["id"]), text)


def notify_destination_recovered(worker_key: str) -> None:
    """Send one recovery notification when a destination returns after an outage."""

    downtime = notify_tracker.recover(worker_key)
    if downtime is None:
        return
    stream_key, _platform_id = split_worker_key(worker_key)
    user = get_user_by_stream_key(stream_key)
    if user is None:
        return
    reconnects = int(worker_telemetry(worker_key).get("reconnect_count") or 0)
    text = build_destination_recovered_message(
        _platform_label_for_worker(worker_key),
        downtime_seconds=int(downtime),
        reconnects=reconnects,
    )
    _send_user_telegram(int(user["id"]), text)


def clear_worker_pending_restart(worker_key: str) -> None:
    """Clear scheduled auto-restart countdown for a worker."""

    ffmpeg_pending_restarts.pop(worker_key, None)


def set_worker_pending_restart(worker_key: str, delay_seconds: float, reason: str) -> None:
    """Publish next auto-restart time for dashboard countdown."""

    ffmpeg_pending_restarts[worker_key] = {
        "next_restart_at": datetime.now(timezone.utc).timestamp() + max(0.0, delay_seconds),
        "delay_seconds": max(0.0, delay_seconds),
        "reason": reason[:200],
    }


def process_snapshot(worker_key: str, entry: dict[str, Any]) -> dict[str, Any]:
    """Serialize one destination worker entry for APIs."""

    process: Any = entry["process"]
    return_code = process.poll()
    progress_path = entry.get("progress_path") or (
        progress_path_for_log(Path(entry["log_path"])) if entry.get("log_path") else None
    )
    progress = parse_ffmpeg_progress(progress_path)
    stream_key = str(entry.get("stream_key") or split_worker_key(worker_key)[0])
    platform_id = str(entry.get("platform_id") or split_worker_key(worker_key)[1])

    marker = progress.get("out_time_ms")
    if marker is not None and marker != entry.get("last_progress_marker"):
        entry["last_progress_marker"] = marker
        entry["last_progress_at"] = utc_now_iso()

    telemetry = worker_telemetry(worker_key)
    bitrate_raw = str(progress.get("bitrate") or "").strip()
    bitrate_kbps = parse_bitrate_kbps(bitrate_raw)
    width, height = parse_resolution_wh(str(entry.get("resolution") or ""))
    started_at = str(entry.get("started_at") or "")
    last_progress_at = str(entry.get("last_progress_at") or "") or None
    progress_age = uptime_seconds_from_iso(last_progress_at) if last_progress_at else None

    pending = ffmpeg_pending_restarts.get(worker_key) or {}
    next_restart_in = None
    if pending.get("next_restart_at") is not None:
        next_restart_in = max(0, int(float(pending["next_restart_at"]) - time.time()))

    return {
        "worker_key": worker_key,
        "stream_key": stream_key,
        "platform_id": platform_id,
        "platform_title": entry.get("platform_title") or platform_title(platform_id),
        "pid": process.pid,
        "status": "running" if return_code is None else "exited",
        "return_code": return_code,
        "started_at": started_at or None,
        "uptime_seconds": uptime_seconds_from_iso(started_at),
        "session_uptime_seconds": uptime_seconds_from_iso(str(telemetry.get("session_started_at") or "")),
        "destinations": entry.get("destinations", 0),
        "log_path": str(entry.get("log_path") or ""),
        "progress_path": str(progress_path or ""),
        "frame": progress["frame"],
        "fps": progress["fps"],
        "bitrate": bitrate_raw or None,
        "bitrate_kbps": bitrate_kbps,
        "speed": progress["speed"] or None,
        "dropped_frames": progress["dropped_frames"],
        "resolution": entry.get("resolution") or None,
        "width": width,
        "height": height,
        "progress": progress["progress"],
        "restart_count": int(telemetry.get("restart_count") or 0),
        "reconnect_count": int(telemetry.get("reconnect_count") or 0),
        "last_error": telemetry.get("last_error"),
        "last_error_at": telemetry.get("last_error_at"),
        "last_progress_at": last_progress_at,
        "progress_age_seconds": progress_age,
        "next_restart_in_seconds": next_restart_in,
        "worker_state": "stopping" if entry.get("stopping") else ("running" if return_code is None else "exited"),
    }


def aggregate_process_snapshot(stream_key: str, workers: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Build a stream-level compatibility snapshot from per-destination workers."""

    if not workers:
        return None

    running = [worker for worker in workers if worker.get("status") == "running"]
    sample = running[0] if running else workers[0]
    frame_values = [int(worker.get("frame") or 0) for worker in workers]
    return {
        "stream_key": stream_key,
        "pid": sample.get("pid"),
        "status": "running" if running else "exited",
        "return_code": None if running else sample.get("return_code"),
        "started_at": sample.get("started_at"),
        "destinations": len(workers),
        "workers_running": len(running),
        "workers_total": len(workers),
        "log_path": sample.get("log_path") or "",
        "progress_path": sample.get("progress_path") or "",
        "frame": max(frame_values) if frame_values else 0,
        "fps": sample.get("fps"),
        "bitrate": sample.get("bitrate") or "",
        "speed": sample.get("speed") or "",
        "dropped_frames": sample.get("dropped_frames"),
        "resolution": sample.get("resolution") or "",
        "progress": sample.get("progress") or "",
        "workers": workers,
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
        "title": "Custom",
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
    workers_by_platform: dict[str, dict[str, Any]],
    recent_by_platform: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build client-facing per-platform status with destination-local metrics."""

    if user is None:
        return []

    statuses: list[dict[str, Any]] = []
    stream_key = str(user.get("stream_key") or "")
    for config in PLATFORM_STATUS_CONFIGS:
        platform_id = str(config["id"])
        worker = workers_by_platform.get(platform_id)
        recent = recent_by_platform.get(platform_id)
        worker_key = worker_key_for(stream_key, platform_id) if stream_key else ""
        telemetry = worker_telemetry(worker_key) if worker_key else {}
        pending = ffmpeg_pending_restarts.get(worker_key) if worker_key else None
        worker_running = bool(worker and worker.get("status") == "running")
        worker_stopping = bool(worker and worker.get("worker_state") == "stopping")
        worker_exited = bool(worker and worker.get("status") == "exited")
        recent_failed = bool(recent and recent.get("return_code") not in {None, 0})
        active = bool(user.get(config["active_field"]))
        configured = platform_configured(user, config)
        source = worker or recent or {}

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
        elif worker_stopping:
            state = "stopping"
            label = "Останавливается"
            color = "yellow"
            reason = ""
        elif pending and not worker_running:
            state = "reconnecting"
            label = "Переподключение"
            color = "yellow"
            reason = str(pending.get("reason") or telemetry.get("last_error") or "Автоперезапуск worker")
        elif worker_running:
            state = "live"
            label = "В эфире"
            color = "green"
            reason = ""
        elif worker_exited or recent_failed or telemetry.get("last_error"):
            state = "error"
            label = "Ошибка"
            color = "red"
            reason = str(telemetry.get("last_error") or "FFmpeg завершился. Откройте ошибки рестрима.")
        else:
            state = "starting"
            label = "Запускается"
            color = "yellow"
            reason = "FFmpeg еще не отдал статус."

        next_restart_in = None
        if pending and pending.get("next_restart_at") is not None:
            next_restart_in = max(0, int(float(pending["next_restart_at"]) - time.time()))

        # Never expose stream keys / RTMP URLs here — only operator metrics.
        statuses.append(
            {
                "id": config["id"],
                "title": config["title"],
                "destination_type": config["id"],
                "active": active,
                "configured": configured,
                "state": state,
                "label": label,
                "color": color,
                "reason": reason,
                "uptime_seconds": source.get("uptime_seconds"),
                "session_uptime_seconds": source.get("session_uptime_seconds")
                or uptime_seconds_from_iso(str(telemetry.get("session_started_at") or "")),
                "worker_pid": source.get("pid"),
                "bitrate": source.get("bitrate"),
                "bitrate_kbps": source.get("bitrate_kbps"),
                "width": source.get("width"),
                "height": source.get("height"),
                "resolution": source.get("resolution"),
                "fps": source.get("fps"),
                "restart_count": int(
                    source.get("restart_count")
                    if source.get("restart_count") is not None
                    else telemetry.get("restart_count") or 0
                ),
                "reconnect_count": int(
                    source.get("reconnect_count")
                    if source.get("reconnect_count") is not None
                    else telemetry.get("reconnect_count") or 0
                ),
                "last_error": telemetry.get("last_error") or source.get("last_error"),
                "last_error_at": telemetry.get("last_error_at") or source.get("last_error_at"),
                "last_progress_at": source.get("last_progress_at"),
                "progress_age_seconds": source.get("progress_age_seconds"),
                "next_restart_in_seconds": next_restart_in,
                "worker_state": source.get("worker_state") or state,
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
        entries = [
            entry
            for worker_key, entry in active_processes.items()
            if worker_key_matches_stream(worker_key, stream_key)
        ]
        for entry in entries:
            entry["resolution"] = resolution
    logger.info("Detected resolution for %s: %s", stream_key, resolution)


def stream_status_payload(stream_key: str) -> dict[str, Any]:
    """Build the client-facing stream status payload."""

    user = get_active_user_by_stream_key(stream_key)
    with process_lock:
        publisher = active_publishers.get(stream_key)
        worker_entries = [
            (worker_key, entry)
            for worker_key, entry in active_processes.items()
            if worker_key_matches_stream(worker_key, stream_key)
        ]
        recent_entries = [
            item for item in recent_processes if item.get("stream_key") == stream_key
        ]

    workers = [process_snapshot(worker_key, entry) for worker_key, entry in worker_entries]
    process = aggregate_process_snapshot(stream_key, workers)
    recent = recent_entries[0] if recent_entries else None
    workers_by_platform = {
        str(worker.get("platform_id") or ""): worker
        for worker in workers
        if worker.get("platform_id")
    }
    recent_by_platform: dict[str, dict[str, Any]] = {}
    for item in recent_entries:
        platform_id = str(item.get("platform_id") or "")
        if platform_id and platform_id not in recent_by_platform:
            recent_by_platform[platform_id] = item
    platform_statuses = build_platform_statuses(user, publisher, workers_by_platform, recent_by_platform)
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
    running_workers = int(process.get("workers_running") or 0) if process else 0

    if not publisher:
        color = "red"
        label = "Нет входящего потока"
        message = "VideoCoder не публикует поток в SRS или SRS еще не прислал on_publish."
    elif destinations == 0:
        color = "yellow"
        label = "Есть поток - рестрим не запущен"
        message = ""
    elif process and process.get("status") == "running" and running_workers >= destinations:
        color = "green" if frame > 0 else "yellow"
        label = "Рестрим работает" if frame > 0 else "FFmpeg запущен, ждем кадры"
        message = "FFmpeg отправляет поток на активные площадки."
    elif process and process.get("status") == "running":
        color = "yellow"
        label = "Часть рестримов работает"
        message = "Один или несколько FFmpeg-воркеров не активны. Проверьте статусы площадок."
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
        "workers": workers,
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
        worker_snapshots = [
            process_snapshot(worker_key, entry)
            for worker_key, entry in active_processes.items()
        ]
        publishers = [{"stream_key": key, **value} for key, value in active_publishers.items()]
        recent = recent_processes[:20]
    by_stream: dict[str, list[dict[str, Any]]] = {}
    for worker in worker_snapshots:
        by_stream.setdefault(str(worker.get("stream_key") or ""), []).append(worker)
    streams = [
        aggregate
        for stream_key, workers in by_stream.items()
        if (aggregate := aggregate_process_snapshot(stream_key, workers)) is not None
    ]
    return {
        "code": 0,
        "streams": streams,
        "workers": worker_snapshots,
        "publishers": publishers,
        "recent": recent,
    }


def stream_logs(stream_key: str, lines: int = 80) -> dict[str, Any]:
    """Return the latest FFmpeg log lines for an active or recent stream."""

    with process_lock:
        entry = active_processes.get(stream_key)
        if entry is None:
            matching_entries = [
                candidate
                for worker_key, candidate in active_processes.items()
                if worker_key_matches_stream(worker_key, stream_key)
            ]
            entry = max(
                matching_entries,
                key=lambda candidate: str(candidate.get("started_at") or ""),
                default=None,
            )
        recent_entry = next(
            (
                item
                for item in recent_processes
                if item.get("stream_key") == stream_key or item.get("worker_key") == stream_key
            ),
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
    response_stream_key = (
        str(entry.get("stream_key") or "")
        if entry
        else str(recent_entry.get("stream_key") or "") if recent_entry else stream_key
    )
    response_worker_key = (
        str(entry.get("worker_key") or "")
        if entry
        else str(recent_entry.get("worker_key") or "") if recent_entry else ""
    )
    return {
        "code": 0,
        "stream_key": response_stream_key,
        "worker_key": response_worker_key,
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
        "workers": streams_payload["workers"],
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


def read_process_environ(pid: int) -> dict[str, str] | None:
    """Read NUL-separated environ for a Linux process."""

    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return None
    environ: dict[str, str] = {}
    for part in raw.split(b"\0"):
        if not part or b"=" not in part:
            continue
        key, value = part.split(b"=", 1)
        environ[key.decode("utf-8", "replace")] = value.decode("utf-8", "replace")
    return environ


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
        environ = read_process_environ(pid)
        if not environ or environ.get("RESTREAM_WORKER") != "1":
            continue
        stream_key = str(environ.get("RESTREAM_STREAM_KEY") or "").strip()
        platform_id = str(environ.get("RESTREAM_PLATFORM") or "").strip()
        if not stream_key or not platform_id:
            continue
        args = read_process_cmdline(pid)
        if not args:
            continue
        binary_name = Path(args[0]).name
        if binary_name != ffmpeg_name and binary_name != "ffmpeg":
            continue
        workers.append(
            {
                "pid": pid,
                "stream_key": stream_key,
                "platform_id": platform_id,
                "worker_key": worker_key_for(stream_key, platform_id),
                "args": args,
                "progress_path": extract_progress_path_from_ffmpeg_args(args),
                "destinations": count_flv_outputs_in_ffmpeg_args(args),
            }
        )
    return workers


def attach_monitor_threads(
    worker_key: str,
    process: Any,
    progress_path: Path,
    input_url: str | None = None,
) -> None:
    """Start monitor/stall(/optional probe) threads for a managed FFmpeg process."""

    stream_key, _platform_id = split_worker_key(worker_key)
    threading.Thread(
        target=monitor_process,
        args=(worker_key, process),
        name=f"ffmpeg-monitor-{worker_key}",
        daemon=True,
    ).start()
    threading.Thread(
        target=watch_ffmpeg_stall,
        args=(worker_key, process, progress_path),
        name=f"ffmpeg-stall-{worker_key}",
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
    platform_id = str(worker["platform_id"])
    worker_key = str(worker.get("worker_key") or worker_key_for(stream_key, platform_id))
    pid = int(worker["pid"])
    process = ExternalProcess(pid)
    if process.poll() is not None:
        return False

    if worker.get("log_path"):
        log_path = Path(str(worker["log_path"]))
    else:
        log_path = find_latest_log_path_for_worker(stream_key, platform_id) or log_path_for_worker(
            stream_key,
            platform_id,
        )
    if worker.get("progress_path"):
        progress_path = Path(str(worker["progress_path"]))
    else:
        progress_path = progress_path_for_log(log_path)
    destinations = int(worker.get("destinations") or 0)
    published_at = utc_now_iso()
    generation = bump_ffmpeg_generation(worker_key)

    with process_lock:
        telemetry = worker_telemetry(worker_key)
        if not telemetry.get("session_started_at"):
            telemetry["session_started_at"] = published_at
        clear_worker_pending_restart(worker_key)
        active_processes[worker_key] = {
            "worker_key": worker_key,
            "stream_key": stream_key,
            "platform_id": platform_id,
            "platform_title": platform_title(platform_id),
            "process": process,
            "started_at": published_at,
            "destinations": destinations or 1,
            "log_path": log_path,
            "progress_path": progress_path,
            "resolution": "",
            "generation": generation,
            "adopted": True,
        }
        publisher = active_publishers.setdefault(
            stream_key,
            {
                "published_at": published_at,
                "destinations": 0,
                "ffmpeg_started": True,
                "adopted": True,
            },
        )
        publisher["destinations"] = max(int(publisher.get("destinations") or 0), len([
            key for key in active_processes if worker_key_matches_stream(key, stream_key)
        ]))
        publisher["ffmpeg_started"] = True

    attach_monitor_threads(worker_key, process, Path(progress_path))
    logger.warning(
        "Adopted orphan FFmpeg pid=%s stream=%s platform=%s",
        pid,
        stream_key,
        platform_id,
    )
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
        worker_key = str(worker.get("worker_key") or worker_key_for(stream_key, str(worker.get("platform_id") or "")))
        pid = int(worker["pid"])
        user = get_active_user_by_stream_key(stream_key)
        duplicate = worker_key in seen_keys
        if not adopt or user is None or duplicate:
            kill_process_pid(pid)
            stats["killed"] += 1
            logger.warning(
                "Killed orphan FFmpeg pid=%s worker=%s (reason=%s)",
                pid,
                worker_key,
                "duplicate" if duplicate else ("policy_kill" if user else "unknown_user"),
            )
            continue
        if adopt_ffmpeg_worker(worker):
            seen_keys.add(worker_key)
            stats["adopted"] += 1
        else:
            stats["killed"] += 1

    persisted = load_publishers_state()
    for stream_key, meta in persisted.items():
        with process_lock:
            already_active = any(
                worker_key_matches_stream(worker_key, stream_key)
                for worker_key in active_processes
            )
        if already_active:
            continue
        user = get_active_user_by_stream_key(stream_key)
        if user is None:
            forget_publisher(stream_key)
            continue
        allowed, _message = validate_destination_limit(user)
        destinations = get_enabled_destination_specs(user)
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
                stats["restarted"] += len(destinations)
                seen_keys.update(worker_key_for(stream_key, spec["id"]) for spec in destinations)
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
    destination_url: str,
    progress_path: Path,
) -> list[str]:
    """Build one FFmpeg command for a single destination output."""

    input_url = SRS_INPUT_URL_TEMPLATE.format(stream_key=stream_key)
    timeout_us = str(FFMPEG_RW_TIMEOUT_US)
    return [
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
        # Per-output network timeout so a hung RTMP target can fail this worker.
        "-f",
        "flv",
        "-rw_timeout",
        timeout_us,
        destination_url,
    ]


def bump_ffmpeg_generation(worker_key: str) -> int:
    """Invalidate in-flight FFmpeg starts for a worker key and return the new generation."""

    with process_lock:
        next_generation = ffmpeg_worker_generations.get(worker_key, 0) + 1
        ffmpeg_worker_generations[worker_key] = next_generation
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


def stop_worker(
    worker_key: str,
    *,
    clear_restart_state: bool = False,
    stopped_by: str = "admin_or_webhook",
) -> bool:
    """Terminate one running FFmpeg worker."""

    bump_ffmpeg_generation(worker_key)
    with process_lock:
        clear_worker_pending_restart(worker_key)
        entry = active_processes.get(worker_key)
        if entry is not None:
            entry["stopping"] = True
        entry = active_processes.pop(worker_key, None)

    if entry is None:
        return False

    process: Any = entry["process"]
    return_code = process.poll()
    if return_code is not None:
        logger.info("FFmpeg worker %s already exited with code %s", worker_key, return_code)
    else:
        logger.info("Stopping FFmpeg worker %s", worker_key)
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            logger.warning("FFmpeg worker %s did not stop gracefully; killing it", worker_key)
            process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning("FFmpeg worker %s did not exit after kill", worker_key)

    snapshot = process_snapshot(worker_key, entry)
    snapshot["ended_at"] = utc_now_iso()
    snapshot["stopped_by"] = stopped_by
    with process_lock:
        recent_processes.insert(0, snapshot)
        del recent_processes[50:]
    if clear_restart_state:
        with process_lock:
            ffmpeg_restart_events.pop(worker_key, None)
    return True


def stop_process(stream_key: str, *, clear_restart_state: bool = False) -> bool:
    """Terminate all running FFmpeg workers for a stream key."""

    with process_lock:
        worker_keys = [
            worker_key
            for worker_key in active_processes
            if worker_key_matches_stream(worker_key, stream_key)
        ]
        inactive_keys = [
            worker_key_for(stream_key, str(config["id"]))
            for config in PLATFORM_STATUS_CONFIGS
            if worker_key_for(stream_key, str(config["id"])) not in worker_keys
        ]
    # Single invalidation path: bump_ffmpeg_generation / stop_worker only.
    for worker_key in inactive_keys:
        bump_ffmpeg_generation(worker_key)
    stopped = False
    for worker_key in worker_keys:
        stopped = stop_worker(
            worker_key,
            clear_restart_state=clear_restart_state,
            stopped_by="admin_or_webhook",
        ) or stopped
    if clear_restart_state:
        clear_ffmpeg_restart_state(stream_key)
    return stopped


def clear_ffmpeg_restart_state(stream_key: str) -> None:
    """Reset FFmpeg auto-restart counters when a publish session ends."""

    with process_lock:
        keys = {
            worker_key
            for worker_key in list(ffmpeg_restart_events)
            if worker_key_matches_stream(worker_key, stream_key)
        }
        keys.update(
            worker_key
            for worker_key in list(ffmpeg_pending_restarts)
            if worker_key_matches_stream(worker_key, stream_key)
        )
        keys.update(
            worker_key
            for worker_key in list(ffmpeg_worker_telemetry)
            if worker_key_matches_stream(worker_key, stream_key)
        )
        for worker_key in keys:
            ffmpeg_restart_events.pop(worker_key, None)
            clear_worker_pending_restart(worker_key)
            ffmpeg_worker_telemetry.pop(worker_key, None)


def next_restart_delay_seconds(worker_key: str, *, consume: bool = True) -> float | None:
    """Return backoff delay or None when rolling restart budget is exhausted.

    When consume=True, records the restart attempt in the rolling window.
    Call with consume=False to preview budget without burning an attempt.
    """

    now = time.time()
    window_start = now - FFMPEG_RESTART_WINDOW_SECONDS
    with process_lock:
        events = [stamp for stamp in ffmpeg_restart_events.get(worker_key, []) if stamp >= window_start]
        if FFMPEG_MAX_RESTARTS > 0 and len(events) >= FFMPEG_MAX_RESTARTS:
            ffmpeg_restart_events[worker_key] = events
            return None
        attempt = len(events) + 1
        if consume:
            events.append(now)
            ffmpeg_restart_events[worker_key] = events
        else:
            ffmpeg_restart_events[worker_key] = events
    # 2, 4, 8, 16, 32 ... capped by MAX (default base=2).
    delay = min(
        FFMPEG_RESTART_BACKOFF_MAX_SECONDS,
        FFMPEG_RESTART_BACKOFF_BASE_SECONDS * (2 ** max(0, attempt - 1)),
    )
    return max(delay, FFMPEG_RESTART_DELAY_SECONDS)


def mark_worker_stable_if_needed(worker_key: str) -> None:
    """Clear rolling restart history after a stable healthy period."""

    with process_lock:
        entry = active_processes.get(worker_key)
        if not entry:
            return
        started_at = str(entry.get("started_at") or "")
        process = entry.get("process")
        if process is None or process.poll() is not None:
            return
    try:
        started = datetime.fromisoformat(started_at)
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - started).total_seconds()
    except ValueError:
        return
    if age < FFMPEG_STABLE_RESET_SECONDS:
        return
    with process_lock:
        ffmpeg_restart_events.pop(worker_key, None)


def maybe_restart_ffmpeg(worker_key: str, return_code: int) -> None:
    """Restart one FFmpeg worker while SRS still reports an active publisher."""

    if return_code == 0 or FFMPEG_MAX_RESTARTS <= 0:
        return

    stream_key, platform_id = split_worker_key(worker_key)
    with process_lock:
        still_published = stream_key in active_publishers
        generation_before_sleep = ffmpeg_worker_generations.get(worker_key, 0)

    if not still_published:
        return

    # Preview budget first so aborted restarts do not burn the rolling window.
    delay = next_restart_delay_seconds(worker_key, consume=False)
    if delay is None:
        critical_error = (
            f"temporarily disabled: >{FFMPEG_MAX_RESTARTS} restarts "
            f"in {int(FFMPEG_RESTART_WINDOW_SECONDS)}s"
        )
        record_worker_error(worker_key, critical_error)
        notify_destination_critical(worker_key, critical_error)
        logger.error(
            "FFmpeg worker %s exceeded %s restarts in %ss; temporarily disabled",
            worker_key,
            FFMPEG_MAX_RESTARTS,
            int(FFMPEG_RESTART_WINDOW_SECONDS),
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

    destinations = get_enabled_destination_specs(user)
    destination = next((spec for spec in destinations if spec["id"] == platform_id), None)
    if destination is None:
        return

    with process_lock:
        set_worker_pending_restart(
            worker_key,
            delay,
            f"auto-restart after exit code {return_code}",
        )
    notify_destination_reconnecting(worker_key, int(delay))

    if delay:
        time.sleep(delay)

    with process_lock:
        clear_worker_pending_restart(worker_key)
        if stream_key not in active_publishers:
            return
        if ffmpeg_worker_generations.get(worker_key, 0) != generation_before_sleep:
            return
        current = active_processes.get(worker_key)
        if current and current.get("process") is not None and current["process"].poll() is None:
            # A newer healthy worker already replaced the crashed one.
            return

    # Consume budget only when we are about to start a replacement worker.
    if next_restart_delay_seconds(worker_key, consume=True) is None:
        return

    with process_lock:
        telemetry = worker_telemetry(worker_key)
        telemetry["restart_count"] = int(telemetry.get("restart_count") or 0) + 1

    logger.warning(
        "Restarting FFmpeg worker %s after exit code %s (backoff %.1fs)",
        worker_key,
        return_code,
        delay,
    )
    try:
        start_ffmpeg_worker(stream_key, destination)
    except Exception as exc:
        record_worker_error(worker_key, f"auto-restart failed: {exc}")
        notify_destination_critical(worker_key, f"auto-restart failed: {exc}")
        logger.exception("Failed to auto-restart FFmpeg worker %s", worker_key)


def monitor_process(worker_key: str, process: Any) -> None:
    """Remove a worker from active_processes when FFmpeg exits by itself."""

    return_code = process.wait()
    unexpected_exit = False
    with process_lock:
        current_entry = active_processes.get(worker_key)
        if current_entry and current_entry.get("process") is process:
            unexpected_exit = True
            snapshot = process_snapshot(worker_key, current_entry)
            snapshot["ended_at"] = utc_now_iso()
            recent_processes.insert(0, snapshot)
            del recent_processes[50:]
            active_processes.pop(worker_key, None)

    if return_code == 0:
        logger.info("FFmpeg worker %s finished successfully", worker_key)
    elif unexpected_exit:
        with process_lock:
            telemetry = worker_telemetry(worker_key)
            telemetry["reconnect_count"] = int(telemetry.get("reconnect_count") or 0) + 1
        error = f"FFmpeg exited with code {return_code}"
        record_worker_error(worker_key, error)
        preview_delay = next_restart_delay_seconds(worker_key, consume=False)
        notify_destination_error(
            worker_key,
            error,
            restart_in_seconds=int(preview_delay) if preview_delay is not None else None,
        )
        logger.error("FFmpeg worker %s exited with code %s", worker_key, return_code)
        maybe_restart_ffmpeg(worker_key, return_code)
    else:
        logger.info("FFmpeg worker %s exited with code %s after being stopped", worker_key, return_code)


def watch_ffmpeg_stall(
    worker_key: str,
    process: Any,
    progress_path: Path,
) -> None:
    """Conservatively kill FFmpeg only after prolonged frozen muxer progress."""

    if FFMPEG_STALL_TIMEOUT_SECONDS <= 0:
        return

    if FFMPEG_STALL_GRACE_SECONDS:
        time.sleep(FFMPEG_STALL_GRACE_SECONDS)

    last_marker = ""
    last_change = time.monotonic()
    frozen_samples = 0
    saw_progress = False
    stream_key, _platform_id = split_worker_key(worker_key)

    while process.poll() is None:
        mark_worker_stable_if_needed(worker_key)
        with process_lock:
            current = active_processes.get(worker_key)
            still_ours = bool(
                current
                and current.get("process") is process
                and current.get("generation") == ffmpeg_worker_generations.get(worker_key)
            )
            still_published = stream_key in active_publishers
        if not still_ours or not still_published:
            return

        marker = ""
        try:
            if progress_path.is_file():
                metrics = parse_ffmpeg_progress(progress_path)
                # Prefer muxer timeline; frame alone may pause during still scenes.
                marker = str(metrics.get("out_time_ms") or "")
                if marker and marker != "0":
                    saw_progress = True
        except OSError:
            marker = ""

        now = time.monotonic()
        if marker and marker != last_marker:
            last_marker = marker
            last_change = now
            frozen_samples = 0
            with process_lock:
                current = active_processes.get(worker_key)
                if current and current.get("process") is process:
                    current["last_progress_marker"] = marker
                    current["last_progress_at"] = utc_now_iso()
        elif saw_progress:
            frozen_samples += 1
            if (
                frozen_samples >= FFMPEG_STALL_MIN_SAMPLES
                and now - last_change >= FFMPEG_STALL_TIMEOUT_SECONDS
            ):
                logger.error(
                    "FFmpeg worker %s complete hang suspected: process alive but out_time_ms frozen "
                    "for %ss after progress was observed; killing worker",
                    worker_key,
                    int(FFMPEG_STALL_TIMEOUT_SECONDS),
                )
                try:
                    process.kill()
                except OSError:
                    logger.exception("Failed to kill stalled FFmpeg worker %s", worker_key)
                return

        time.sleep(FFMPEG_STALL_POLL_SECONDS)


def normalize_destination_specs(destinations: list[Any]) -> list[dict[str, str]]:
    """Normalize destination specs while tolerating legacy URL lists."""

    specs: list[dict[str, str]] = []
    for index, destination in enumerate(destinations):
        if isinstance(destination, dict):
            platform_id = str(destination.get("id") or "").strip()
            title = str(destination.get("title") or platform_id or f"Destination {index + 1}").strip()
            url = str(destination.get("url") or "").strip()
        else:
            platform_id = f"custom{index + 1}"
            title = f"Destination {index + 1}"
            url = str(destination or "").strip()
        if not platform_id or not url:
            continue
        specs.append({"id": platform_id, "title": title, "url": url})
    return specs


def redact_rtmp_url(url: str) -> str:
    """Mask stream-key tail of an RTMP URL for safe logging."""

    if "://" not in url:
        return "[REDACTED]"
    scheme, rest = url.split("://", 1)
    parts = rest.rstrip("/").split("/")
    if len(parts) >= 2:
        parts[-1] = "*******"
    return f"{scheme}://{'/'.join(parts)}"


def redacted_ffmpeg_command(command: list[str]) -> list[str]:
    """Redact destination RTMP URLs before logging command lines."""

    redacted_command = command[:]
    for index, value in enumerate(redacted_command):
        if value.startswith(("rtmp://", "rtmps://")):
            # Keep input host visible enough for ops, but always mask key tails.
            redacted_command[index] = redact_rtmp_url(value)
    return redacted_command


def start_ffmpeg_worker(stream_key: str, destination: dict[str, str]) -> bool:
    """Start or replace one destination FFmpeg worker."""

    platform_id = str(destination["id"])
    destination_url = str(destination["url"])
    worker_key = worker_key_for(stream_key, platform_id)

    with process_lock:
        if stream_key not in active_publishers:
            logger.info("Skip FFmpeg start for %s: publisher is not active", worker_key)
            return False

    stop_worker(worker_key)
    generation = bump_ffmpeg_generation(worker_key)

    with process_lock:
        if stream_key not in active_publishers:
            logger.info("Abort FFmpeg start for %s after unpublish", worker_key)
            return False
        if ffmpeg_worker_generations.get(worker_key) != generation:
            return False

    input_url = SRS_INPUT_URL_TEMPLATE.format(stream_key=stream_key)
    log_path = log_path_for_worker(stream_key, platform_id)
    progress_path = progress_path_for_log(log_path)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    progress_path.write_text("", encoding="utf-8")

    command = build_ffmpeg_command(stream_key, destination_url, progress_path)
    redacted_command = redacted_ffmpeg_command(command)
    logger.info(
        "Starting FFmpeg worker %s (%s): %s",
        worker_key,
        destination.get("title") or platform_id,
        " ".join(redacted_command),
    )

    with log_path.open("ab") as log_file:
        log_file.write(f"[{utc_now_iso()}] Starting: {' '.join(redacted_command)}\n".encode("utf-8"))
        log_file.flush()
        env = os.environ.copy()
        env.update(
            {
                "RESTREAM_WORKER": "1",
                "RESTREAM_STREAM_KEY": stream_key,
                "RESTREAM_PLATFORM": platform_id,
            }
        )
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=log_file,
            start_new_session=True,
            env=env,
        )

    with process_lock:
        if (
            ffmpeg_worker_generations.get(worker_key) != generation
            or stream_key not in active_publishers
        ):
            logger.warning(
                "Discarding superseded/unpublished FFmpeg worker start for %s pid=%s",
                worker_key,
                process.pid,
            )
            try:
                process.kill()
            except OSError:
                pass
            return False
        started_at = utc_now_iso()
        telemetry = worker_telemetry(worker_key)
        if not telemetry.get("session_started_at"):
            telemetry["session_started_at"] = started_at
        clear_worker_pending_restart(worker_key)
        active_processes[worker_key] = {
            "worker_key": worker_key,
            "stream_key": stream_key,
            "platform_id": platform_id,
            "platform_title": destination.get("title") or platform_title(platform_id),
            "destination_url": destination_url,
            "process": process,
            "started_at": started_at,
            "destinations": 1,
            "log_path": log_path,
            "progress_path": progress_path,
            "resolution": "",
            "generation": generation,
        }
        publisher = active_publishers.get(stream_key)
        if publisher is not None:
            publisher["ffmpeg_started"] = True

    attach_monitor_threads(worker_key, process, progress_path, input_url=input_url)
    persist_publishers_state()
    # If this start heals an outage that did not go through maybe_restart_ffmpeg,
    # still clear tracker state (no message when there was no prior outage).
    notify_destination_recovered(worker_key)
    return True


def start_ffmpeg(stream_key: str, destinations: list[Any]) -> bool:
    """Start or replace FFmpeg workers for all enabled destinations of a stream."""

    destination_specs = normalize_destination_specs(destinations)
    if not destination_specs:
        logger.info("Stream %s accepted without restream destinations", stream_key)
        return False

    with process_lock:
        if stream_key not in active_publishers:
            logger.info("Skip start_ffmpeg for %s: publisher is not active", stream_key)
            return False

    # SRS may retry callbacks; ensure one fresh worker exists per destination.
    stop_process(stream_key)

    started_count = 0
    errors: list[str] = []
    for destination in destination_specs:
        with process_lock:
            if stream_key not in active_publishers:
                logger.info("Abort remaining FFmpeg starts for %s after unpublish", stream_key)
                break
        try:
            if start_ffmpeg_worker(stream_key, destination):
                started_count += 1
        except FileNotFoundError:
            raise
        except Exception as exc:
            errors.append(str(exc))
            logger.exception(
                "Failed to start FFmpeg worker for %s platform=%s",
                stream_key,
                destination.get("id"),
            )

    if started_count == 0 and errors:
        raise RuntimeError("; ".join(errors))

    with process_lock:
        publisher = active_publishers.get(stream_key)
        if publisher is not None:
            publisher["ffmpeg_started"] = started_count > 0
            publisher["destinations"] = len(destination_specs)
            publisher["workers_started"] = started_count
    persist_publishers_state()
    return started_count > 0


def sync_live_restream_worker(user: dict[str, Any]) -> bool:
    """Apply destination changes without restarting healthy unchanged workers."""

    stream_key = str(user.get("stream_key") or "")
    if not stream_key:
        return False

    with process_lock:
        publisher = active_publishers.get(stream_key)

    if publisher is None:
        return False

    destinations = get_enabled_destination_specs(user)
    desired_by_id = {str(spec["id"]): spec for spec in destinations}

    with process_lock:
        current_keys = [
            worker_key
            for worker_key in list(active_processes)
            if worker_key_matches_stream(worker_key, stream_key)
        ]

    for worker_key in current_keys:
        _, platform_id = split_worker_key(worker_key)
        desired = desired_by_id.get(platform_id)
        with process_lock:
            entry = active_processes.get(worker_key)
            current_url = str(entry.get("destination_url") or "") if entry else ""
            process = entry.get("process") if entry else None
            running = bool(process is not None and process.poll() is None)
        if desired is None:
            stop_worker(worker_key, clear_restart_state=True, stopped_by="settings_sync")
            continue
        if running and current_url == str(desired["url"]):
            continue
        try:
            start_ffmpeg_worker(stream_key, desired)
        except Exception:
            logger.exception("Failed to sync worker %s", worker_key)

    for platform_id, destination in desired_by_id.items():
        worker_key = worker_key_for(stream_key, platform_id)
        with process_lock:
            entry = active_processes.get(worker_key)
            process = entry.get("process") if entry else None
            running = bool(process is not None and process.poll() is None)
        if running:
            continue
        try:
            start_ffmpeg_worker(stream_key, destination)
        except Exception:
            logger.exception("Failed to start missing worker %s", worker_key)

    with process_lock:
        current_publisher = active_publishers.get(stream_key)
        running_count = sum(
            1
            for worker_key, entry in active_processes.items()
            if worker_key_matches_stream(worker_key, stream_key)
            and entry.get("process") is not None
            and entry["process"].poll() is None
        )
        if current_publisher is not None:
            current_publisher["destinations"] = len(destinations)
            current_publisher["ffmpeg_started"] = running_count > 0
            current_publisher["workers_started"] = running_count
            started = running_count > 0
        else:
            started = False
    persist_publishers_state()
    return started


@app.post("/api/auth/login")
def api_login(
    payload: LoginPayload,
    response: Response,
    request: Request,
) -> dict[str, Any]:
    """Authenticate a user for the future web frontend."""

    enforce_auth_rate_limit(request)
    user = authenticate_user(payload.username, payload.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid credentials or blocked account")
    return create_token_response(user, response)


@app.post("/api/auth/register")
def api_register(
    payload: RegisterPayload,
    response: Response,
    request: Request,
) -> dict[str, Any]:
    """Register a client user and return an API session token."""

    enforce_auth_rate_limit(request)
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
def api_forgot_password(payload: ForgotPasswordPayload, request: Request) -> dict[str, Any]:
    """Request password reset without revealing whether the account exists."""

    enforce_auth_rate_limit(request)
    # Always return the same external payload (no email/username oracle).
    generic_message = (
        "Если аккаунт существует и для него настроен email, "
        "мы отправили инструкции по восстановлению пароля."
    )
    user, token = create_password_reset_token(payload.identifier)
    if user is not None and token is not None:
        try:
            send_password_reset_email(user, token)
        except Exception:
            logger.exception("Password reset email failed")
    return {"code": 0, "message": generic_message}


@app.post("/api/auth/reset-password")
def api_reset_password(payload: PasswordResetPayload, request: Request) -> dict[str, Any]:
    """Reset password using a valid email reset token."""

    enforce_auth_rate_limit(request)
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


@app.get("/api/me/destination-profiles")
def api_list_destination_profiles(
    user: dict[str, Any] = Depends(get_current_api_user),
) -> dict[str, Any]:
    """List destination profiles owned by the current user (secrets masked)."""

    profiles = list_destination_profiles(int(user["id"]))
    return {"code": 0, "profiles": profiles}


@app.post("/api/me/destination-profiles")
def api_create_destination_profile(
    payload: DestinationProfileCreatePayload,
    user: dict[str, Any] = Depends(get_current_api_user),
) -> dict[str, Any]:
    """Create a reusable destination profile."""

    ok, message, profile = create_destination_profile(
        int(user["id"]),
        payload.name,
        payload.platform_id,
        base_url=payload.base_url,
        stream_key=payload.stream_key,
    )
    if not ok or profile is None:
        raise HTTPException(status_code=400, detail=message)
    return {"code": 0, "message": message, "profile": profile}


@app.put("/api/me/destination-profiles/{profile_id}")
@app.patch("/api/me/destination-profiles/{profile_id}")
def api_update_destination_profile(
    profile_id: int,
    payload: DestinationProfileUpdatePayload,
    user: dict[str, Any] = Depends(get_current_api_user),
) -> dict[str, Any]:
    """Update a profile. Masked/empty stream_key keeps the existing secret."""

    updates = model_to_dict(payload, exclude_unset=True)
    ok, message, profile = update_destination_profile(
        int(user["id"]),
        profile_id,
        name=updates.get("name"),
        base_url=updates.get("base_url"),
        stream_key=updates.get("stream_key"),
    )
    if not ok or profile is None:
        status = 404 if message == "Профиль не найден." else 400
        raise HTTPException(status_code=status, detail=message)
    return {"code": 0, "message": message, "profile": profile}


@app.delete("/api/me/destination-profiles/{profile_id}")
def api_delete_destination_profile(
    profile_id: int,
    user: dict[str, Any] = Depends(get_current_api_user),
) -> dict[str, Any]:
    """Delete a destination profile owned by the current user."""

    ok, message = delete_destination_profile(int(user["id"]), profile_id)
    if not ok:
        raise HTTPException(status_code=404 if message == "Профиль не найден." else 400, detail=message)
    return {"code": 0, "message": message}


@app.post("/api/me/destination-profiles/{profile_id}/apply")
def api_apply_destination_profile(
    profile_id: int,
    payload: DestinationProfileApplyPayload,
    user: dict[str, Any] = Depends(get_current_api_user),
) -> dict[str, Any]:
    """Copy profile credentials into the existing flat destination slot."""

    ok, message, fresh_user = apply_destination_profile(
        int(user["id"]),
        profile_id,
        activate=False,
    )
    if not ok or fresh_user is None:
        status = 404 if message == "Профиль не найден." else 400
        raise HTTPException(status_code=status, detail=message)

    ffmpeg_started = False
    if payload.activate:
        platform_id = None
        for candidate in ("yt", "vk", "rt", "tg", "custom"):
            if int(fresh_user.get(f"{candidate}_profile_id") or 0) == int(profile_id):
                platform_id = candidate
                break
        if platform_id is None:
            raise HTTPException(status_code=400, detail="Не удалось определить площадку профиля")
        settings = {f"{platform_id}_active": 1}
        pending = build_pending_user_settings(fresh_user, settings)
        allowed, limit_message = validate_destination_limit(pending)
        if not allowed:
            raise HTTPException(status_code=400, detail=limit_message)
        urls_ok, urls_message = validate_destination_urls(pending)
        if not urls_ok:
            raise HTTPException(status_code=400, detail=urls_message)
        update_restream_settings(int(user["id"]), settings)
        fresh_user = get_user_by_id(int(user["id"]))
        if fresh_user is None:
            raise HTTPException(status_code=500, detail="Account refresh failed")
        ffmpeg_started = sync_live_restream_worker(fresh_user)

    return {
        "code": 0,
        "message": message,
        "user": public_user(fresh_user),
        "ffmpeg_started": ffmpeg_started,
    }


@app.get("/api/me/notifications")
def api_get_notifications(user: dict[str, Any] = Depends(get_current_api_user)) -> dict[str, Any]:
    """Return Telegram notification settings (no bot token, chat id masked)."""

    settings = get_notification_settings(int(user["id"]))
    return {"code": 0, **settings, **notification_public_status()}


@app.put("/api/me/notifications")
@app.patch("/api/me/notifications")
def api_update_notifications(
    payload: NotificationSettingsPayload,
    user: dict[str, Any] = Depends(get_current_api_user),
) -> dict[str, Any]:
    """Enable/disable Telegram notifications and set Telegram login (@username)."""

    ok, message, settings = update_notification_settings(
        int(user["id"]),
        telegram_enabled=payload.telegram_enabled,
        telegram_username=payload.telegram_username,
        telegram_chat_id=payload.telegram_chat_id,
    )
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return {"code": 0, "message": message, **settings, **notification_public_status()}


@app.post("/api/me/notifications/telegram/test")
def api_test_telegram_notification(
    user: dict[str, Any] = Depends(get_current_api_user),
) -> dict[str, Any]:
    """Send a test Telegram message using server-side bot token."""

    if not telegram_bot_configured():
        raise HTTPException(status_code=400, detail="Telegram bot token is not configured on server")
    fresh = get_user_by_id(int(user["id"])) or {}
    # Allow testing even if notifications are currently off.
    chat_id = str(fresh.get("notify_tg_chat_id") or "").strip()
    username = str(fresh.get("notify_tg_username") or "").strip().lstrip("@")
    target = chat_id or (f"@{username}" if username else "")
    if not target:
        raise HTTPException(
            status_code=400,
            detail="Укажите логин Telegram (@username) и сохраните настройки",
        )
    if not chat_id and username:
        # Re-resolve and persist before sending.
        ok_settings, resolve_message, _settings = update_notification_settings(
            int(user["id"]),
            telegram_username=f"@{username}",
            resolve_username=True,
        )
        if not ok_settings:
            raise HTTPException(status_code=400, detail=resolve_message)
        fresh = get_user_by_id(int(user["id"])) or {}
        chat_id = str(fresh.get("notify_tg_chat_id") or "").strip()
        target = chat_id or f"@{username}"
    ok, message = send_telegram_message(
        target,
        f"✅ Restream: проверка Telegram для {user.get('username')}",
    )
    if not ok:
        detail = message
        if "Start" not in message and "не найден" not in message.lower():
            detail = (
                f"{message}. Откройте бота в Telegram, нажмите Start "
                "и повторите проверку."
            )
        raise HTTPException(status_code=502, detail=detail)
    return {
        "code": 0,
        "message": "Тестовое сообщение отправлено.",
        **get_notification_settings(int(user["id"])),
        **notification_public_status(),
    }


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
    """Create a consistent database backup (SQLite backup API or pg_dump)."""

    return {"code": 0, "backup": create_database_backup()}


@app.get("/health")
def health() -> JSONResponse:
    """Readiness endpoint used by Docker/systemd health checks."""

    db_ok, db_detail = check_database()
    with process_lock:
        active_count = len(active_processes)
    payload = {
        "status": "ok" if db_ok else "degraded",
        "active_streams": active_count,
        "database": {
            "ok": db_ok,
            "backend": DATABASE_BACKEND,
            "detail": db_detail,
        },
        "srs_webhook_configured": bool(SRS_WEBHOOK_SECRET)
        and not any(marker in SRS_WEBHOOK_SECRET.lower() for marker in FORBIDDEN_SECRET_MARKERS),
    }
    return JSONResponse(status_code=200 if db_ok else 503, content=payload)


@app.post("/on_publish")
async def on_publish(request: Request) -> JSONResponse:
    """Authorize SRS publishing and start FFmpeg workers for enabled platforms."""

    verify_srs_webhook(request)
    payload = await parse_srs_payload(request)
    stream_key = extract_stream_key(payload)
    if not stream_key:
        return srs_error("stream key is required", status_code=400)

    user = get_active_user_by_stream_key(stream_key)
    if user is None:
        logger.warning("Rejected publish for inactive or unknown stream key: %s", stream_key)
        return srs_error("stream is not allowed")

    destinations = get_enabled_destination_specs(user)
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
    notify_stream_event(stream_key, "started")

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
    notify_stream_event(stream_key, "stopped")
    return JSONResponse(
        status_code=200,
        content={"code": 0, "stream_key": stream_key, "stopped": stopped},
    )


if __name__ == "__main__":
    import uvicorn

    # Default to loopback; production should sit behind nginx / Docker port publish.
    host = os.getenv("RESTREAM_BIND_HOST", "127.0.0.1")
    port = int(os.getenv("RESTREAM_BIND_PORT", "8000"))
    uvicorn.run(app, host=host, port=port)
