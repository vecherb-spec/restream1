"""Server-side Telegram Bot notifications for Restream events.

Bot token is read only from environment variables and never exposed via API.
Never log the bot token or raw secrets.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

logger = logging.getLogger("restream.telegram")

_TOKEN_ENV_KEYS = ("RESTREAM_TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_TOKEN")
DEFAULT_EVENT_COOLDOWN_SECONDS = 45.0
_TELEGRAM_USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")


class NotificationGate:
    """Debounce identical Telegram event families for the same destination."""

    def __init__(self, cooldown_seconds: float = DEFAULT_EVENT_COOLDOWN_SECONDS) -> None:
        self.cooldown_seconds = float(cooldown_seconds)
        self._lock = threading.Lock()
        self._last_sent: dict[str, float] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            previous = self._last_sent.get(key)
            if previous is not None and (now - previous) < self.cooldown_seconds:
                return False
            self._last_sent[key] = now
            return True

    def reset(self) -> None:
        with self._lock:
            self._last_sent.clear()


class DestinationNotifyTracker:
    """Track per-destination outage phases so watchdog ticks do not spam Telegram."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # worker_key -> state
        self._states: dict[str, dict[str, Any]] = {}

    def begin_error(self, worker_key: str) -> bool:
        """Return True when ERROR notification should be sent (first time in outage)."""

        with self._lock:
            state = self._states.setdefault(worker_key, {})
            if state.get("error_notified"):
                return False
            state["error_notified"] = True
            state["reconnect_notified"] = False
            state["critical_notified"] = False
            state["phase"] = "error"
            state["outage_started"] = time.monotonic()
            return True

    def begin_reconnecting(self, worker_key: str) -> bool:
        """Return True when RECONNECTING notification should be sent (once per outage)."""

        with self._lock:
            state = self._states.setdefault(worker_key, {})
            if state.get("reconnect_notified"):
                return False
            # Entering reconnect without a prior error still counts as one outage.
            if "outage_started" not in state:
                state["outage_started"] = time.monotonic()
            state["error_notified"] = True
            state["reconnect_notified"] = True
            state["phase"] = "reconnecting"
            return True

    def begin_critical(self, worker_key: str) -> bool:
        """Return True when CRITICAL notification should be sent (once per outage)."""

        with self._lock:
            state = self._states.setdefault(worker_key, {})
            if state.get("critical_notified"):
                return False
            if "outage_started" not in state:
                state["outage_started"] = time.monotonic()
            state["error_notified"] = True
            state["critical_notified"] = True
            state["phase"] = "critical"
            return True

    def recover(self, worker_key: str) -> float | None:
        """Clear outage state. Returns downtime seconds when recovering from an outage."""

        with self._lock:
            state = self._states.pop(worker_key, None)
        if not state:
            return None
        if not state.get("error_notified") and not state.get("reconnect_notified"):
            return None
        started = state.get("outage_started")
        if started is None:
            return 0.0
        return max(0.0, time.monotonic() - float(started))

    def reset(self) -> None:
        with self._lock:
            self._states.clear()


def telegram_bot_token() -> str:
    """Return configured bot token (empty when unset)."""

    for key in _TOKEN_ENV_KEYS:
        value = (os.getenv(key) or "").strip()
        if value:
            return value
    return ""


def telegram_bot_configured() -> bool:
    """True when a bot token is present server-side."""

    return bool(telegram_bot_token())


def normalize_telegram_username(value: str | None) -> tuple[bool, str, str]:
    """Normalize a Telegram login to bare username (without @).

    Returns (ok, username_or_empty, error_message).
    """

    raw = (value or "").strip()
    if not raw:
        return True, "", ""
    if raw.startswith("https://t.me/") or raw.startswith("http://t.me/"):
        raw = raw.split("t.me/", 1)[1]
    raw = raw.split("?", 1)[0].strip().strip("/")
    if raw.startswith("@"):
        raw = raw[1:]
    raw = raw.strip()
    if not _TELEGRAM_USERNAME_RE.fullmatch(raw):
        return (
            False,
            "",
            "Укажите логин Telegram вида @username (латиница, 5–32 символа).",
        )
    return True, raw, ""


def format_telegram_username(username: str | None) -> str:
    value = (username or "").strip().lstrip("@")
    return f"@{value}" if value else ""


def _redact_token(text: str, token: str) -> str:
    if token and token in text:
        return text.replace(token, "[redacted]")
    return text


def _telegram_api_call(method: str, params: dict[str, Any], *, timeout: float = 8.0) -> tuple[bool, dict[str, Any], str]:
    """Call Telegram Bot API method. Never logs the bot token."""

    token = telegram_bot_token()
    if not token:
        return False, {}, "Telegram bot token is not configured"
    body = urllib.parse.urlencode({key: str(value) for key, value in params.items()}).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            detail = ""
        detail = _redact_token(detail, token)
        logger.warning("Telegram API %s HTTP error status=%s detail=%s", method, exc.code, detail or "-")
        return False, {}, f"Telegram API HTTP {exc.code}"
    except Exception as exc:
        logger.warning("Telegram API %s request failed: %s", method, type(exc).__name__)
        return False, {}, "Telegram API request failed"

    if not payload.get("ok"):
        desc = _redact_token(str(payload.get("description") or "unknown")[:200], token)
        logger.warning("Telegram API %s rejected: %s", method, desc)
        return False, payload, desc or "Telegram API rejected request"
    result = payload.get("result")
    return True, (result if isinstance(result, dict) else {"result": result}), "ok"


def resolve_telegram_chat_id(username_or_chat: str, *, timeout: float = 8.0) -> tuple[bool, str, str]:
    """Resolve @username (or numeric id) to a Telegram chat id via getChat."""

    target = (username_or_chat or "").strip()
    if not target:
        return False, "", "Telegram target is empty"
    if target.lstrip("-").isdigit():
        return True, target, "ok"
    ok_user, username, error = normalize_telegram_username(target)
    if not ok_user:
        return False, "", error
    ok, result, message = _telegram_api_call("getChat", {"chat_id": f"@{username}"}, timeout=timeout)
    if not ok:
        hint = (
            "Не удалось найти чат по логину. Откройте бота в Telegram, нажмите Start "
            "и повторите проверку."
        )
        return False, "", hint if "chat not found" in message.lower() or "not found" in message.lower() else message
    chat_id = result.get("id")
    if chat_id is None:
        return False, "", "Telegram getChat did not return chat id"
    return True, str(chat_id), "ok"


def send_telegram_message(chat_id: str, text: str, *, timeout: float = 8.0) -> tuple[bool, str]:
    """Send a Telegram message. Never logs token or secret-bearing URLs."""

    token = telegram_bot_token()
    if not token:
        return False, "Telegram bot token is not configured"
    chat_id = (chat_id or "").strip()
    if not chat_id:
        return False, "Telegram chat id is empty"
    # Usernames must be resolved for private chats; numeric ids send directly.
    if not chat_id.lstrip("-").isdigit():
        ok, resolved, message = resolve_telegram_chat_id(chat_id, timeout=timeout)
        if not ok:
            return False, message
        chat_id = resolved
    ok, _result, message = _telegram_api_call(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text[:3500],
            "disable_web_page_preview": "true",
        },
        timeout=timeout,
    )
    if not ok:
        return False, message if message.startswith("Telegram") else f"Telegram API rejected message"
    return True, "ok"


def build_destination_error_message(
    title: str,
    *,
    error: str,
    restart_in_seconds: int | None = None,
    when: str = "",
) -> str:
    """Format a destination-down Telegram message."""

    lines = [
        f"🔴 {title} недоступен",
        f"Время: {when}" if when else "",
        f"Ошибка: {_short(error)}",
    ]
    if restart_in_seconds is not None:
        lines.append(f"Попытка восстановления: через {int(restart_in_seconds)} сек")
    return "\n".join(line for line in lines if line)


def build_destination_reconnecting_message(
    title: str,
    *,
    restart_in_seconds: int | None = None,
    when: str = "",
) -> str:
    lines = [
        f"🟠 {title}: началось восстановление",
        f"Время: {when}" if when else "",
    ]
    if restart_in_seconds is not None:
        lines.append(f"Повтор через: {int(restart_in_seconds)} сек")
    return "\n".join(line for line in lines if line)


def build_destination_recovered_message(
    title: str,
    *,
    downtime_seconds: int | None = None,
    reconnects: int = 0,
) -> str:
    """Format a destination-recovered Telegram message."""

    lines = [f"🟢 {title} восстановлен"]
    if downtime_seconds is not None:
        lines.append(f"Downtime: {int(downtime_seconds)} сек")
    lines.append(f"Reconnects: {int(reconnects)}")
    return "\n".join(lines)


def build_critical_worker_error_message(title: str, *, error: str, when: str = "") -> str:
    lines = [
        "🆘 Критическая ошибка worker",
        f"Площадка: {title}",
        f"Время: {when}" if when else "",
        f"Ошибка: {_short(error)}",
    ]
    return "\n".join(line for line in lines if line)


def build_stream_started_message(username: str, when: str = "") -> str:
    lines = [f"🟢 Эфир начался", f"Пользователь: {username}"]
    if when:
        lines.append(f"Время: {when}")
    return "\n".join(lines)


def build_stream_stopped_message(username: str, when: str = "") -> str:
    lines = [f"⏹ Эфир полностью остановлен", f"Пользователь: {username}"]
    if when:
        lines.append(f"Время: {when}")
    return "\n".join(lines)


def notification_public_status() -> dict[str, Any]:
    """Safe status fragment for API clients (no token)."""

    return {"telegram_bot_configured": telegram_bot_configured()}


def local_time_hhmmss() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).astimezone().strftime("%H:%M:%S")


def _short(text: str, limit: int = 220) -> str:
    value = (text or "").strip().replace("\n", " ")
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"
