"""Streamlit control panel for the Restream MVP.

Run locally:
    streamlit run app.py

The UI intentionally implements its own page routing through st.session_state.
This keeps authorization rules explicit and avoids exposing admin/client pages
through Streamlit's built-in multipage discovery.
"""

from __future__ import annotations

import os
from typing import Any

import requests
import streamlit as st

from database import (
    YOUTUBE_RTMP_URL,
    authenticate_user,
    create_user,
    get_user_by_id,
    init_db,
    list_users,
    set_user_active,
    update_restream_settings,
)


OBS_SERVER_URL = "rtmp://restream.medialive.ru/live"
BACKEND_URL = os.getenv("RESTREAM_BACKEND_URL", "http://localhost:8000").rstrip("/")


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

    st.subheader("Площадки для рестрима")
    st.caption("Поток отправляется в режиме pass-through (`-c copy`) без перекодирования.")

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
        update_restream_settings(int(user["id"]), settings)
        st.success("Настройки сохранены.")
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
                "Создан": item["created_at"],
            }
            for item in users
        ],
        use_container_width=True,
        hide_index=True,
    )

    st.subheader("Блокировка пользователей")
    client_options = {
        f"{item['id']} — {item['username']} ({'active' if item['is_active'] else 'blocked'})": item
        for item in users
    }
    selected_label = st.selectbox("Пользователь", list(client_options.keys()))
    selected_user = client_options[selected_label]

    col_block, col_unblock = st.columns(2)
    with col_block:
        if st.button("Заблокировать", disabled=not bool_value(selected_user["is_active"])):
            set_user_active(int(selected_user["id"]), False)
            st.success("Пользователь заблокирован.")
            st.rerun()
    with col_unblock:
        if st.button("Разблокировать", disabled=bool_value(selected_user["is_active"])):
            set_user_active(int(selected_user["id"]), True)
            st.success("Пользователь разблокирован.")
            st.rerun()

    st.subheader("Активные эфиры")
    active_streams, error = fetch_active_streams()
    if error:
        st.warning(error)
        st.caption(f"Проверьте, что FastAPI backend запущен по адресу {BACKEND_URL}.")
    elif active_streams:
        st.table(active_streams)
    else:
        st.info("Сейчас нет активных FFmpeg-процессов.")

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
