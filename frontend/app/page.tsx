"use client";

import { useEffect, useMemo, useState } from "react";
import { HlsPreview } from "@/components/HlsPreview";
import {
  OBS_SERVER_URL,
  RestreamSettings,
  StreamStatus,
  User,
  clearToken,
  getMe,
  getSettings,
  getStreamStatus,
  getToken,
  login,
  logout,
  register,
  setToken,
  updateSettings,
  userToSettings,
} from "@/lib/api";

type AuthMode = "login" | "register";

const emptySettings: RestreamSettings = {
  yt_active: false,
  yt_key: "",
  vk_active: false,
  vk_url: "",
  vk_key: "",
  rt_active: false,
  rt_url: "",
  rt_key: "",
  tg_active: false,
  tg_url: "",
  tg_key: "",
  custom_active: false,
  custom_url: "",
  custom_key: "",
};

function Lamp({ color }: { color: StreamStatus["color"] }) {
  return <span className={`lamp ${color}`} aria-label={color} />;
}

function AuthCard({ onAuthenticated }: { onAuthenticated: (token: string, user: User) => void }) {
  const [mode, setMode] = useState<AuthMode>("login");
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    setLoading(true);
    try {
      const response =
        mode === "login"
          ? await login(username, password)
          : await register(username, password, email);
      setToken(response.token);
      onAuthenticated(response.token, response.user);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Ошибка авторизации");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="card" style={{ maxWidth: 460, margin: "80px auto" }}>
      <h1>Restream MediaLive</h1>
      <p className="muted">Новый Next.js кабинет поверх FastAPI API.</p>
      <div className="tabs">
        <button className={`tab ${mode === "login" ? "active" : ""}`} onClick={() => setMode("login")}>
          Вход
        </button>
        <button
          className={`tab ${mode === "register" ? "active" : ""}`}
          onClick={() => setMode("register")}
        >
          Регистрация
        </button>
      </div>
      <form className="form" onSubmit={submit}>
        <label className="field">
          Логин
          <input value={username} onChange={(event) => setUsername(event.target.value)} required />
        </label>
        {mode === "register" && (
          <label className="field">
            Email
            <input
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
            />
          </label>
        )}
        <label className="field">
          Пароль
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
          />
        </label>
        {error && <div className="error">{error}</div>}
        <button className="button" disabled={loading}>
          {loading ? "Подождите..." : mode === "login" ? "Войти" : "Зарегистрироваться"}
        </button>
      </form>
    </div>
  );
}

function StreamStatusCard({ token }: { token: string }) {
  const [status, setStatus] = useState<StreamStatus | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;

    async function loadStatus() {
      try {
        const nextStatus = await getStreamStatus(token);
        if (active) {
          setStatus(nextStatus);
          setError("");
        }
      } catch (requestError) {
        if (active) {
          setError(requestError instanceof Error ? requestError.message : "Ошибка статуса");
        }
      }
    }

    loadStatus();
    const intervalId = window.setInterval(loadStatus, 1000);
    return () => {
      active = false;
      window.clearInterval(intervalId);
    };
  }, [token]);

  if (error) {
    return <div className="error">{error}</div>;
  }

  if (!status) {
    return <div className="card">Загрузка статуса...</div>;
  }

  return (
    <div className="card">
      <div className="status">
        <Lamp color={status.color} />
        {status.label}
      </div>
      <p className="muted">{status.message}</p>
      <div className="grid">
        <div className="metric">
          Bitrate
          <strong>{status.bitrate || "-"}</strong>
        </div>
        <div className="metric">
          FPS
          <strong>{status.fps == null ? "-" : status.fps.toFixed(1)}</strong>
        </div>
        <div className="metric">
          Разрешение
          <strong>{status.resolution || "определяется"}</strong>
        </div>
        <div className="metric">
          Площадок
          <strong>{status.destinations}</strong>
        </div>
      </div>
    </div>
  );
}

function SettingsForm({
  token,
  user,
  onSaved,
}: {
  token: string;
  user: User;
  onSaved: (user: User) => void;
}) {
  const [settings, setSettings] = useState<RestreamSettings>(() => userToSettings(user));
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const platforms = useMemo(
    () => [
      { prefix: "vk", title: "VK" },
      { prefix: "rt", title: "Rutube" },
      { prefix: "tg", title: "Telegram" },
      { prefix: "custom", title: "Custom RTMP" },
    ],
    [],
  );

  function update<K extends keyof RestreamSettings>(key: K, value: RestreamSettings[K]) {
    setSettings((current) => ({ ...current, [key]: value }));
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    setMessage("");
    try {
      const response = await updateSettings(token, settings);
      onSaved(response.user);
      setMessage("Настройки сохранены. Перезапустите эфир в OBS.");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Не удалось сохранить");
    }
  }

  return (
    <form className="card" onSubmit={save}>
      <h2>Площадки рестрима</h2>
      <div className="alert">После сохранения настроек остановите и заново запустите эфир в OBS.</div>

      <div className="settings-row">
        <label>
          <input
            type="checkbox"
            checked={settings.yt_active}
            onChange={(event) => update("yt_active", event.target.checked)}
          />{" "}
          YouTube
        </label>
        <input value="rtmp://a.rtmp.youtube.com/live2" disabled />
        <input
          placeholder="YouTube stream key"
          value={settings.yt_key}
          onChange={(event) => update("yt_key", event.target.value)}
        />
      </div>

      {platforms.map(({ prefix, title }) => {
        const activeKey = `${prefix}_active` as keyof RestreamSettings;
        const urlKey = `${prefix}_url` as keyof RestreamSettings;
        const streamKey = `${prefix}_key` as keyof RestreamSettings;
        return (
          <div className="settings-row" key={prefix}>
            <label>
              <input
                type="checkbox"
                checked={Boolean(settings[activeKey])}
                onChange={(event) => update(activeKey, event.target.checked)}
              />{" "}
              {title}
            </label>
            <input
              placeholder="RTMP URL"
              value={String(settings[urlKey])}
              onChange={(event) => update(urlKey, event.target.value)}
            />
            <input
              placeholder="Stream key"
              value={String(settings[streamKey])}
              onChange={(event) => update(streamKey, event.target.value)}
            />
          </div>
        );
      })}

      {error && <div className="error">{error}</div>}
      {message && <div className="alert">{message}</div>}
      <button className="button">Сохранить изменения</button>
    </form>
  );
}

function Dashboard({
  token,
  user,
  onLogout,
  onUserChange,
}: {
  token: string;
  user: User;
  onLogout: () => void;
  onUserChange: (user: User) => void;
}) {
  async function handleLogout() {
    await logout(token).catch(() => undefined);
    clearToken();
    onLogout();
  }

  return (
    <div className="shell">
      <header className="header">
        <div>
          <h1>Личный кабинет</h1>
          <p className="muted">Здравствуйте, {user.username}</p>
        </div>
        <button className="button secondary" onClick={handleLogout}>
          Выйти
        </button>
      </header>

      <div className="grid">
        <div className="card">
          <h2>OBS</h2>
          <p className="muted">Server</p>
          <strong>{OBS_SERVER_URL}</strong>
          <p className="muted">Stream Key</p>
          <strong>{user.stream_key}</strong>
        </div>
        <StreamStatusCard token={token} />
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h2>Превью</h2>
        <HlsPreview streamKey={user.stream_key} />
      </div>

      <div style={{ marginTop: 16 }}>
        <SettingsForm token={token} user={user} onSaved={onUserChange} />
      </div>
    </div>
  );
}

export default function Home() {
  const [token, setCurrentToken] = useState("");
  const [user, setUser] = useState<User | null>(null);

  useEffect(() => {
    const storedToken = getToken();
    if (!storedToken) {
      return;
    }

    getMe(storedToken)
      .then((response) => {
        setCurrentToken(storedToken);
        setUser(response.user);
      })
      .catch(() => clearToken());
  }, []);

  function onAuthenticated(nextToken: string, nextUser: User) {
    setCurrentToken(nextToken);
    setUser(nextUser);
  }

  return (
    <main className="page">
      {token && user ? (
        <Dashboard
          token={token}
          user={user}
          onUserChange={setUser}
          onLogout={() => {
            setCurrentToken("");
            setUser(null);
          }}
        />
      ) : (
        <AuthCard onAuthenticated={onAuthenticated} />
      )}
    </main>
  );
}
