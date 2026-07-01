"""Streamlit control panel for the Restream MVP.

Run locally:
    streamlit run app.py

The UI intentionally implements its own page routing through st.session_state.
This keeps authorization rules explicit and avoids exposing admin/client pages
through Streamlit's built-in multipage discovery.
"""

from __future__ import annotations

import html
import json
import os
from typing import Any

import requests
import streamlit as st
import streamlit.components.v1 as components

from database import (
    YOUTUBE_RTMP_URL,
    authenticate_user,
    create_user,
    get_user_by_id,
    init_db,
    list_users,
    regenerate_user_stream_key,
    set_user_active,
    update_user_password,
    update_restream_settings,
)


OBS_SERVER_URL = "rtmp://restream.medialive.ru/live"
BACKEND_URL = os.getenv("RESTREAM_BACKEND_URL", "http://localhost:8000").rstrip("/")
PREVIEW_HLS_BASE_URL = os.getenv(
    "RESTREAM_PREVIEW_HLS_BASE_URL",
    "https://restream.medialive.ru/srs/live",
).rstrip("/")
PREVIEW_HLS_URL_TEMPLATE = os.getenv("RESTREAM_PREVIEW_HLS_URL_TEMPLATE", "").strip()


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    """Keep password hashes out of Streamlit session state."""

    return {key: value for key, value in user.items() if key != "password"}


def set_logged_in_user(user: dict[str, Any]) -> None:
    """Persist authenticated user context in Streamlit session state."""

    st.session_state["user"] = public_user(user)
    st.session_state["role"] = user["role"]
    st.session_state["authenticated"] = True


def logout() -> None:
    """Clear all authentication-related state."""

    for key in ("user", "role", "authenticated"):
        st.session_state.pop(key, None)
    st.rerun()


def refresh_current_user() -> dict[str, Any] | None:
    """Reload user data from SQLite after profile/settings changes."""

    current_user = st.session_state.get("user")
    if not current_user:
        return None

    fresh_user = get_user_by_id(int(current_user["id"]))
    if not fresh_user or not fresh_user["is_active"]:
        st.warning("Ваш аккаунт отключен. Выполнен выход из системы.")
        logout()
        return None

    set_logged_in_user(fresh_user)
    return st.session_state["user"]


def bool_value(value: Any) -> bool:
    """Convert SQLite 0/1 values to booleans for Streamlit widgets."""

    return bool(int(value or 0))


def is_valid_rtmp_url(value: str) -> bool:
    """Validate the RTMP URL schemes supported by FFmpeg FLV output."""

    normalized_value = value.strip().lower()
    return normalized_value.startswith(("rtmp://", "rtmps://"))


def validate_restream_settings(settings: dict[str, Any]) -> list[str]:
    """Return user-facing validation errors for enabled restream platforms."""

    errors: list[str] = []
    if settings.get("yt_active") and not str(settings.get("yt_key") or "").strip():
        errors.append("YouTube включен, но ключ потока не заполнен.")

    platforms = [
        ("vk", "VK"),
        ("rt", "Rutube"),
        ("tg", "Telegram"),
        ("custom", "Custom RTMP"),
    ]
    for prefix, title in platforms:
        if not settings.get(f"{prefix}_active"):
            continue

        url = str(settings.get(f"{prefix}_url") or "").strip()
        key = str(settings.get(f"{prefix}_key") or "").strip()
        if not url:
            errors.append(f"{title}: RTMP URL обязателен, если площадка включена.")
        elif not is_valid_rtmp_url(url):
            errors.append(f"{title}: RTMP URL должен начинаться с rtmp:// или rtmps://.")
        if not key:
            errors.append(f"{title}: ключ потока обязателен, если площадка включена.")

    return errors


def render_obs_instructions(user: dict[str, Any]) -> None:
    """Show OBS setup guidance directly in the client account."""

    with st.expander("Инструкция для OBS и YouTube", expanded=True):
        st.markdown(
            f"""
            **OBS -> Settings -> Stream**

            - Service: `Custom`
            - Server: `{OBS_SERVER_URL}`
            - Stream Key: `{user["stream_key"]}`

            **OBS -> Settings -> Output -> Streaming**

            - Rate Control: `CBR`
            - Bitrate: `3000-4500 Kbps` для 720p30 или `4500-6000 Kbps` для 1080p30
            - Keyframe Interval: `2`
            - Profile: `high`

            После изменения площадок в кабинете нажмите **Сохранить изменения**,
            полностью остановите эфир в OBS и запустите его заново.
            """
        )


def build_preview_hls_url(stream_key: str) -> str:
    """Build the public HLS preview URL for a stream key."""

    if PREVIEW_HLS_URL_TEMPLATE:
        return PREVIEW_HLS_URL_TEMPLATE.format(stream_key=stream_key)
    return f"{PREVIEW_HLS_BASE_URL}/{stream_key}.m3u8"


def render_hls_preview(stream_key: str) -> None:
    """Render a browser HLS player for the user's own live stream."""

    preview_url = build_preview_hls_url(stream_key)
    preview_url_js = json.dumps(preview_url)
    player_id = f"preview-{html.escape(stream_key, quote=True)}"

    components.html(
        f"""
        <div style="font-family: sans-serif;">
          <div id="{player_id}-wrap" style="display: inline-block; max-width: 100%;">
            <video
              id="{player_id}"
              controls
              muted
              playsinline
              style="width: auto; max-width: 100%; height: auto; max-height: 420px; background: #111; border-radius: 12px;"
            ></video>
          </div>
          <div id="{player_id}-status" style="margin-top: 8px; color: #666; font-size: 14px;">
            Если эфир уже запущен, превью может появиться через 10-30 секунд.
          </div>
        </div>
        <script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
        <script>
          const video = document.getElementById("{player_id}");
          const wrapper = document.getElementById("{player_id}-wrap");
          const statusBox = document.getElementById("{player_id}-status");
          const sourceUrl = {preview_url_js};
          const maxPlayerHeight = 420;

          function setStatus(message) {{
            statusBox.textContent = message;
          }}

          function resizePlayer() {{
            if (!video.videoWidth || !video.videoHeight) {{
              return;
            }}

            const availableWidth = Math.max(280, document.documentElement.clientWidth - 24);
            const aspectRatio = video.videoWidth / video.videoHeight;
            const widthByHeight = maxPlayerHeight * aspectRatio;
            const targetWidth = Math.min(video.videoWidth, widthByHeight, availableWidth);

            video.style.width = `${{Math.round(targetWidth)}}px`;
            wrapper.style.width = video.style.width;
          }}

          video.addEventListener("loadedmetadata", resizePlayer);
          window.addEventListener("resize", resizePlayer);

          if (video.canPlayType("application/vnd.apple.mpegurl")) {{
            video.src = sourceUrl;
            video.addEventListener("loadedmetadata", function () {{
              resizePlayer();
              setStatus("Превью подключено. Нажмите Play.");
            }});
            video.addEventListener("error", function () {{
              setStatus("Пока нет HLS-потока. Проверьте, что OBS запущен и SRS отдает HLS.");
            }});
          }} else if (window.Hls && window.Hls.isSupported()) {{
            const hls = new Hls({{
              lowLatencyMode: true,
              liveSyncDurationCount: 3,
            }});
            hls.loadSource(sourceUrl);
            hls.attachMedia(video);
            hls.on(Hls.Events.MANIFEST_PARSED, function () {{
              resizePlayer();
              setStatus("Превью подключено. Нажмите Play.");
            }});
            hls.on(Hls.Events.ERROR, function (_event, data) {{
              if (data && data.fatal) {{
                setStatus("Пока нет HLS-потока или он недоступен. Обновите превью через несколько секунд.");
              }}
            }});
          }} else {{
            setStatus("Этот браузер не поддерживает HLS. Попробуйте Safari/Chrome/Edge.");
          }}
        </script>
        """,
        height=500,
    )
    st.caption(f"HLS preview URL: `{preview_url}`")


def render_auth_page() -> None:
    """Render login and open registration page."""

    st.title("Restream MediaLive")
    st.caption("MVP SaaS-панель для рестриминга через SRS и FFmpeg pass-through.")

    mode = st.radio(
        "Выберите действие",
        ["Вход", "Регистрация"],
        horizontal=True,
    )

    if mode == "Вход":
        with st.form("login_form"):
            username = st.text_input("Логин")
            password = st.text_input("Пароль", type="password")
            submitted = st.form_submit_button("Войти")

        if submitted:
            user = authenticate_user(username, password)
            if user is None:
                st.error("Неверный логин/пароль или аккаунт заблокирован.")
            else:
                set_logged_in_user(user)
                st.success("Вход выполнен.")
                st.rerun()

        with st.expander("Данные администратора по умолчанию"):
            st.write("Логин: `admin`")
            st.write("Пароль: `admin_password_2026`")

    else:
        with st.form("register_form"):
            username = st.text_input("Логин")
            email = st.text_input("Email")
            password = st.text_input("Пароль", type="password")
            password_repeat = st.text_input("Повторите пароль", type="password")
            submitted = st.form_submit_button("Зарегистрироваться")

        if submitted:
            if password != password_repeat:
                st.error("Пароли не совпадают.")
                return

            success, message, user = create_user(username, password, email)
            if not success or user is None:
                st.error(message)
            else:
                set_logged_in_user(user)
                st.success("Регистрация завершена. Вы вошли в личный кабинет.")
                st.rerun()


def platform_fields(prefix: str, title: str, user: dict[str, Any], url_required: bool = True) -> dict[str, Any]:
    """Render one platform settings block and return its updated values."""

    settings: dict[str, Any] = {}
    with st.expander(title, expanded=False):
        active_field = f"{prefix}_active"
        key_field = f"{prefix}_key"
        settings[active_field] = st.checkbox(
            "Включить",
            value=bool_value(user.get(active_field)),
            key=f"{prefix}_active_widget",
        )
        if url_required:
            url_field = f"{prefix}_url"
            settings[url_field] = st.text_input(
                "RTMP URL",
                value=str(user.get(url_field) or ""),
                key=f"{prefix}_url_widget",
                placeholder="rtmp://example.com/live",
            )
        settings[key_field] = st.text_input(
            "Ключ потока",
            value=str(user.get(key_field) or ""),
            key=f"{prefix}_key_widget",
            type="password",
        )
    return settings


def render_client_dashboard() -> None:
    """Render client account dashboard and restream settings."""

    user = refresh_current_user()
    if user is None:
        return

    st.title("Личный кабинет")
    st.write(f"Здравствуйте, **{user['username']}**.")

    col_url, col_key = st.columns(2)
    with col_url:
        st.text_input("URL сервера для OBS", value=OBS_SERVER_URL, disabled=True)
    with col_key:
        st.text_input("Ключ потока OBS", value=user["stream_key"], disabled=True)

    st.info(
        "В OBS укажите URL сервера и ключ потока выше. "
        "FFmpeg будет запущен автоматически после webhook `/on_publish` от SRS."
    )

    st.subheader("Превью вашего потока")
    st.caption(
        "Превью работает через HLS и обычно отстает от OBS на 10-30 секунд. "
        "Если эфир не запущен, плеер будет пустым."
    )
    render_hls_preview(user["stream_key"])

    render_obs_instructions(user)

    with st.expander("Безопасность stream key", expanded=False):
        st.warning(
            "Генерация нового ключа остановит текущий эфир для старого ключа. "
            "После этого нужно заменить Stream Key в OBS."
        )
        confirm_key_reset = st.checkbox(
            "Я понимаю, что старый stream key перестанет работать.",
            key="client_confirm_stream_key_reset",
        )
        if st.button("Сгенерировать новый stream key", disabled=not confirm_key_reset):
            stop_remote_stream(user["stream_key"])
            success, message, new_stream_key = regenerate_user_stream_key(int(user["id"]))
            if success:
                st.success(f"{message} Новый ключ: {new_stream_key}")
                refresh_current_user()
                st.rerun()
            else:
                st.error(message)

    st.subheader("Площадки для рестрима")
    st.caption("Поток отправляется в режиме pass-through (`-c copy`) без перекодирования.")
    st.warning(
        "После изменения площадок нажмите 'Сохранить изменения' и перезапустите поток в OBS. "
        "Текущий FFmpeg-процесс не перечитывает настройки на лету."
    )

    with st.form("restream_settings_form"):
        settings: dict[str, Any] = {}

        with st.expander("YouTube", expanded=True):
            settings["yt_active"] = st.checkbox(
                "Включить",
                value=bool_value(user.get("yt_active")),
                key="yt_active_widget",
            )
            st.text_input("RTMP URL", value=YOUTUBE_RTMP_URL, disabled=True)
            settings["yt_key"] = st.text_input(
                "Ключ потока",
                value=str(user.get("yt_key") or ""),
                key="yt_key_widget",
                type="password",
            )

        settings.update(platform_fields("vk", "VK", user))
        settings.update(platform_fields("rt", "Rutube", user))
        settings.update(platform_fields("tg", "Telegram", user))
        settings.update(platform_fields("custom", "Custom RTMP", user))

        submitted = st.form_submit_button("Сохранить изменения")

    if submitted:
        errors = validate_restream_settings(settings)
        if errors:
            for error in errors:
                st.error(error)
        else:
            update_restream_settings(int(user["id"]), settings)
            st.success("Настройки сохранены. Остановите и заново запустите эфир в OBS.")
            refresh_current_user()

    st.divider()
    if st.button("Выйти из аккаунта"):
        logout()


def fetch_active_streams() -> tuple[list[dict[str, Any]], str | None]:
    """Load active stream keys from the FastAPI backend."""

    try:
        response = requests.get(f"{BACKEND_URL}/active_streams", timeout=3)
        response.raise_for_status()
        data = response.json()
        return data.get("streams", []), None
    except requests.RequestException as exc:
        return [], f"Не удалось получить активные эфиры: {exc}"
    except ValueError:
        return [], "Backend вернул некорректный JSON."


def fetch_stream_activity() -> tuple[dict[str, list[dict[str, Any]]], str | None]:
    """Load active and recent stream process data from FastAPI."""

    try:
        response = requests.get(f"{BACKEND_URL}/active_streams", timeout=3)
        response.raise_for_status()
        data = response.json()
        return {
            "streams": data.get("streams", []),
            "recent": data.get("recent", []),
        }, None
    except requests.RequestException as exc:
        return {"streams": [], "recent": []}, f"Не удалось получить эфиры: {exc}"
    except ValueError:
        return {"streams": [], "recent": []}, "Backend вернул некорректный JSON."


def stop_remote_stream(stream_key: str) -> tuple[bool, str]:
    """Ask FastAPI to stop an active FFmpeg worker."""

    try:
        response = requests.post(f"{BACKEND_URL}/stop_stream/{stream_key}", timeout=5)
        response.raise_for_status()
        data = response.json()
        if data.get("stopped"):
            return True, "Эфир остановлен."
        return False, "Активный процесс для этого ключа не найден."
    except requests.RequestException as exc:
        return False, f"Не удалось остановить эфир: {exc}"
    except ValueError:
        return False, "Backend вернул некорректный JSON."


def fetch_stream_logs(stream_key: str, lines: int = 80) -> tuple[list[str], str | None]:
    """Load latest FFmpeg log lines for a stream."""

    try:
        response = requests.get(
            f"{BACKEND_URL}/stream_logs/{stream_key}",
            params={"lines": lines},
            timeout=5,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 0:
            return [], data.get("message", "Лог не найден.")
        return data.get("lines", []), None
    except requests.RequestException as exc:
        return [], f"Не удалось получить лог: {exc}"
    except ValueError:
        return [], "Backend вернул некорректный JSON."


def render_admin_dashboard() -> None:
    """Render administrator dashboard."""

    user = refresh_current_user()
    if user is None:
        return

    st.title("Админка Restream")
    st.write(f"Вы вошли как **{user['username']}**.")

    st.subheader("Пользователи")
    users = list_users()
    st.dataframe(
        [
            {
                "ID": item["id"],
                "Логин": item["username"],
                "Email": item["email"],
                "Роль": item["role"],
                "Stream key": item["stream_key"],
                "Активен": bool_value(item["is_active"]),
                "YouTube": bool_value(item["yt_active"]),
                "VK": bool_value(item["vk_active"]),
                "Rutube": bool_value(item["rt_active"]),
                "Telegram": bool_value(item["tg_active"]),
                "Custom": bool_value(item["custom_active"]),
                "Включено площадок": sum(
                    bool_value(item[field])
                    for field in ("yt_active", "vk_active", "rt_active", "tg_active", "custom_active")
                ),
                "Создан": item["created_at"],
            }
            for item in users
        ],
        use_container_width=True,
        hide_index=True,
    )

    st.subheader("Управление пользователем")
    client_options = {
        f"{item['id']} — {item['username']} ({'active' if item['is_active'] else 'blocked'})": item
        for item in users
    }
    selected_label = st.selectbox("Пользователь", list(client_options.keys()))
    selected_user = client_options[selected_label]

    col_block, col_unblock = st.columns(2)
    with col_block:
        if st.button("Заблокировать", disabled=not bool_value(selected_user["is_active"])):
            stopped, stop_message = stop_remote_stream(selected_user["stream_key"])
            set_user_active(int(selected_user["id"]), False)
            if stopped:
                st.success(f"Пользователь заблокирован. {stop_message}")
            else:
                st.success("Пользователь заблокирован.")
                st.caption(stop_message)
            st.rerun()
    with col_unblock:
        if st.button("Разблокировать", disabled=bool_value(selected_user["is_active"])):
            set_user_active(int(selected_user["id"]), True)
            st.success("Пользователь разблокирован.")
            st.rerun()

    with st.form("admin_password_form"):
        st.write("Смена пароля выбранного пользователя")
        new_password = st.text_input("Новый пароль", type="password")
        new_password_repeat = st.text_input("Повторите новый пароль", type="password")
        password_submitted = st.form_submit_button("Обновить пароль")

    if password_submitted:
        if new_password != new_password_repeat:
            st.error("Пароли не совпадают.")
        else:
            success, message = update_user_password(int(selected_user["id"]), new_password)
            if success:
                st.success(message)
            else:
                st.error(message)

    with st.form("admin_stream_key_form"):
        st.write("Сброс stream key выбранного пользователя")
        st.caption(
            "Если пользователь сейчас в эфире, активный FFmpeg-процесс будет остановлен. "
            "Пользователю потребуется вставить новый ключ в OBS."
        )
        confirm_stream_key_reset = st.checkbox(
            f"Подтверждаю сброс stream key для {selected_user['username']}",
            key=f"admin_confirm_stream_key_reset_{selected_user['id']}",
        )
        key_submitted = st.form_submit_button("Сгенерировать новый stream key")

    if key_submitted:
        if not confirm_stream_key_reset:
            st.error("Подтвердите сброс stream key.")
        else:
            stop_remote_stream(selected_user["stream_key"])
            success, message, new_stream_key = regenerate_user_stream_key(int(selected_user["id"]))
            if success:
                st.success(f"{message} Новый ключ: {new_stream_key}")
                st.rerun()
            else:
                st.error(message)

    st.subheader("Активные эфиры")
    activity, error = fetch_stream_activity()
    active_streams = activity["streams"]
    recent_streams = activity["recent"]
    if error:
        st.warning(error)
        st.caption(f"Проверьте, что FastAPI backend запущен по адресу {BACKEND_URL}.")
    elif active_streams:
        st.dataframe(active_streams, use_container_width=True, hide_index=True)
        for stream in active_streams:
            stream_key = stream["stream_key"]
            with st.expander(f"{stream_key} — PID {stream.get('pid')}"):
                col_stop, col_log = st.columns([1, 3])
                with col_stop:
                    if st.button("Остановить эфир", key=f"stop_{stream_key}"):
                        success, message = stop_remote_stream(stream_key)
                        if success:
                            st.success(message)
                            st.rerun()
                        else:
                            st.warning(message)
                with col_log:
                    log_lines, log_error = fetch_stream_logs(stream_key)
                    if log_error:
                        st.caption(log_error)
                    else:
                        st.code("\n".join(log_lines[-80:]) or "Лог пока пуст.", language="text")
    else:
        st.info("Сейчас нет активных FFmpeg-процессов.")

    st.subheader("Недавние завершения FFmpeg")
    if recent_streams:
        st.dataframe(recent_streams, use_container_width=True, hide_index=True)
        selected_recent = st.selectbox(
            "Посмотреть лог завершенного эфира",
            [item["stream_key"] for item in recent_streams],
        )
        log_lines, log_error = fetch_stream_logs(selected_recent)
        if log_error:
            st.caption(log_error)
        else:
            st.code("\n".join(log_lines[-80:]) or "Лог пуст.", language="text")
    else:
        st.caption("Пока нет завершенных FFmpeg-процессов.")

    st.divider()
    if st.button("Выйти из аккаунта"):
        logout()


def main() -> None:
    """Application entrypoint."""

    init_db()
    st.set_page_config(page_title="Restream MediaLive", page_icon="🎥", layout="wide")

    if not st.session_state.get("authenticated"):
        render_auth_page()
        return

    role = st.session_state.get("role")
    if role == "admin":
        render_admin_dashboard()
    elif role == "client":
        render_client_dashboard()
    else:
        st.error("Неизвестная роль пользователя.")
        if st.button("Выйти"):
            logout()


if __name__ == "__main__":
    main()
