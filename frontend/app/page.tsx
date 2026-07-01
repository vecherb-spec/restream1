"use client";

import { useEffect, useMemo, useState } from "react";
import { HlsPreview } from "@/components/HlsPreview";
import {
  BackupInfo,
  OBS_SERVER_URL,
  RestreamSettings,
  StreamProcess,
  StreamPublisher,
  StreamStatus,
  SystemMetrics,
  User,
  createAdminBackup,
  getAdminBackups,
  getAdminStreamLogs,
  getAdminStreams,
  getAdminSystemMetrics,
  getAdminUsers,
  getMe,
  getStreamStatus,
  getStreamStatusEventsUrl,
  login,
  logout,
  register,
  resetAdminUserPassword,
  resetAdminUserStreamKey,
  setAdminUserActive,
  stopAdminStream,
  updateAdminUserPlan,
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

function formatBytes(value?: number) {
  if (value == null) {
    return "-";
  }
  let size = value;
  for (const unit of ["B", "KB", "MB", "GB", "TB"]) {
    if (size < 1024 || unit === "TB") {
      return unit === "B" ? `${Math.round(size)} B` : `${size.toFixed(1)} ${unit}`;
    }
    size /= 1024;
  }
  return `${size.toFixed(1)} TB`;
}

function Lamp({ color }: { color: StreamStatus["color"] }) {
  return <span className={`lamp ${color}`} aria-label={color} />;
}

function countEnabledDestinations(settings: RestreamSettings) {
  let count = 0;
  if (settings.yt_active && settings.yt_key.trim()) count += 1;
  if (settings.vk_active && settings.vk_url.trim() && settings.vk_key.trim()) count += 1;
  if (settings.rt_active && settings.rt_url.trim() && settings.rt_key.trim()) count += 1;
  if (settings.tg_active && settings.tg_url.trim() && settings.tg_key.trim()) count += 1;
  if (settings.custom_active && settings.custom_url.trim() && settings.custom_key.trim()) count += 1;
  return count;
}

function AuthCard({ onAuthenticated }: { onAuthenticated: (user: User) => void }) {
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
      onAuthenticated(response.user);
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

function StreamStatusCard() {
  const [status, setStatus] = useState<StreamStatus | null>(null);
  const [error, setError] = useState("");
  const [transport, setTransport] = useState<"sse" | "fallback">("sse");

  useEffect(() => {
    let active = true;
    let fallbackIntervalId: number | undefined;

    getStreamStatus()
      .then((nextStatus) => {
        if (active) {
          setStatus(nextStatus);
          setError("");
        }
      })
      .catch((requestError) => {
        if (active) {
          setError(requestError instanceof Error ? requestError.message : "Ошибка статуса");
        }
      });

    const eventSource = new EventSource(getStreamStatusEventsUrl(), { withCredentials: true });
    eventSource.onmessage = (event) => {
      if (!active) {
        return;
      }
      try {
        setStatus(JSON.parse(event.data) as StreamStatus);
        setTransport("sse");
        setError("");
      } catch {
        setError("Некорректный SSE payload статуса");
      }
    };
    eventSource.onerror = () => {
      if (!active) {
        return;
      }
      setTransport("fallback");
      if (!fallbackIntervalId) {
        fallbackIntervalId = window.setInterval(() => {
          getStreamStatus()
            .then((nextStatus) => {
              if (active) {
                setStatus(nextStatus);
              }
            })
            .catch(() => undefined);
        }, 3000);
      }
    };

    return () => {
      active = false;
      eventSource.close();
      if (fallbackIntervalId) {
        window.clearInterval(fallbackIntervalId);
      }
    };
  }, []);

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
      <p className="muted">
        {status.message} · обновление: {transport === "sse" ? "SSE live" : "fallback polling"}
      </p>
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
  user,
  onSaved,
}: {
  user: User;
  onSaved: (user: User) => void;
}) {
  const [settings, setSettings] = useState<RestreamSettings>(() => userToSettings(user));
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const enabledDestinations = countEnabledDestinations(settings);
  const maxDestinations = user.max_destinations ?? 1;

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
      const response = await updateSettings(settings);
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
      <p className="muted">
        Тариф `{user.plan || "free"}`: активно {enabledDestinations} из {maxDestinations} разрешенных площадок.
      </p>
      {enabledDestinations > maxDestinations && (
        <div className="error">
          Вы выбрали больше площадок, чем разрешено тарифом. Сохранение будет отклонено.
        </div>
      )}

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
  user,
  onLogout,
  onUserChange,
}: {
  user: User;
  onLogout: () => void;
  onUserChange: (user: User) => void;
}) {
  async function handleLogout() {
    await logout().catch(() => undefined);
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
          <p className="muted">Тариф</p>
          <strong>
            {user.plan || "free"} · до {user.max_destinations ?? 1} активных площадок
          </strong>
        </div>
        <StreamStatusCard />
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h2>Превью</h2>
        <HlsPreview streamKey={user.stream_key} />
      </div>

      <div style={{ marginTop: 16 }}>
        <SettingsForm user={user} onSaved={onUserChange} />
      </div>
    </div>
  );
}

function AdminDashboard({
  user,
  onLogout,
}: {
  user: User;
  onLogout: () => void;
}) {
  const [users, setUsers] = useState<User[]>([]);
  const [metrics, setMetrics] = useState<SystemMetrics | null>(null);
  const [streams, setStreams] = useState<StreamProcess[]>([]);
  const [publishers, setPublishers] = useState<StreamPublisher[]>([]);
  const [recent, setRecent] = useState<StreamProcess[]>([]);
  const [backups, setBackups] = useState<BackupInfo[]>([]);
  const [logLines, setLogLines] = useState<string[]>([]);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  async function loadAdminData() {
    try {
      const [usersResponse, metricsResponse, streamsResponse, backupsResponse] = await Promise.all([
        getAdminUsers(),
        getAdminSystemMetrics(),
        getAdminStreams(),
        getAdminBackups(),
      ]);
      setUsers(usersResponse.users);
      setMetrics(metricsResponse);
      setStreams(streamsResponse.streams);
      setPublishers(streamsResponse.publishers);
      setRecent(streamsResponse.recent);
      setBackups(backupsResponse.backups);
      setError("");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Ошибка загрузки админки");
    }
  }

  useEffect(() => {
    const initialLoadId = window.setTimeout(loadAdminData, 0);
    const intervalId = window.setInterval(loadAdminData, 5000);
    return () => {
      window.clearTimeout(initialLoadId);
      window.clearInterval(intervalId);
    };
  }, []);

  async function handleLogout() {
    await logout().catch(() => undefined);
    onLogout();
  }

  async function toggleUser(userId: number, isActive: boolean) {
    await setAdminUserActive(userId, isActive);
    setMessage(isActive ? "Пользователь разблокирован." : "Пользователь заблокирован.");
    await loadAdminData();
  }

  async function resetPassword(userId: number) {
    const newPassword = window.prompt("Новый пароль минимум 8 символов");
    if (!newPassword) {
      return;
    }
    await resetAdminUserPassword(userId, newPassword);
    setMessage("Пароль обновлен.");
  }

  async function updatePlan(userId: number) {
    const plan = window.prompt("Название тарифа", "free");
    if (!plan) {
      return;
    }
    const maxValue = window.prompt("Максимум активных площадок", "1");
    if (maxValue == null) {
      return;
    }
    const maxDestinations = Number.parseInt(maxValue, 10);
    if (Number.isNaN(maxDestinations) || maxDestinations < 0) {
      setError("Лимит площадок должен быть неотрицательным числом.");
      return;
    }
    await updateAdminUserPlan(userId, plan, maxDestinations);
    setMessage("Тариф обновлен.");
    await loadAdminData();
  }

  async function resetStreamKey(userId: number) {
    if (!window.confirm("Сбросить stream key пользователя? Старый ключ перестанет работать.")) {
      return;
    }
    const response = await resetAdminUserStreamKey(userId);
    setMessage(`Новый stream key: ${response.stream_key}`);
    await loadAdminData();
  }

  async function stopStream(streamKey: string) {
    await stopAdminStream(streamKey);
    setMessage("Эфир остановлен.");
    await loadAdminData();
  }

  async function showLogs(streamKey: string) {
    const response = await getAdminStreamLogs(streamKey);
    setLogLines(response.lines || []);
  }

  async function createBackup() {
    const response = await createAdminBackup();
    setMessage(`Backup создан: ${response.backup.filename}`);
    await loadAdminData();
  }

  return (
    <div className="shell">
      <header className="header">
        <div>
          <h1>Админка Restream</h1>
          <p className="muted">Вы вошли как {user.username}. Данные обновляются каждые 5 секунд.</p>
        </div>
        <button className="button secondary" onClick={handleLogout}>
          Выйти
        </button>
      </header>

      {error && <div className="error">{error}</div>}
      {message && <div className="alert">{message}</div>}

      <div className="grid">
        <div className="card">
          <h2>CPU</h2>
          <div className="metric">
            Load/core
            <strong>{metrics?.cpu.load_1_per_core ?? "-"}</strong>
          </div>
          <p className="muted">
            Load: {metrics?.cpu.load_1 ?? "-"} / {metrics?.cpu.load_5 ?? "-"} /{" "}
            {metrics?.cpu.load_15 ?? "-"}
          </p>
        </div>
        <div className="card">
          <h2>RAM</h2>
          <div className="metric">
            Used
            <strong>{metrics?.memory.used_percent ?? "-"}%</strong>
          </div>
          <p className="muted">
            {formatBytes(metrics?.memory.used_bytes)} / {formatBytes(metrics?.memory.total_bytes)}
          </p>
        </div>
        <div className="card">
          <h2>Disk</h2>
          <div className="metric">
            Used
            <strong>{metrics?.disk.used_percent ?? "-"}%</strong>
          </div>
          <p className="muted">
            {formatBytes(metrics?.disk.used_bytes)} / {formatBytes(metrics?.disk.total_bytes)}
          </p>
        </div>
        <div className="card">
          <h2>Services</h2>
          <p>API: {metrics?.services.api ? "OK" : "FAIL"}</p>
          <p>SRS RTMP: {metrics?.services.srs_rtmp_1935 ? "OK" : "FAIL"}</p>
          <p>SRS HLS: {metrics?.services.srs_hls_8080 ? "OK" : "FAIL"}</p>
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h2>Backup SQLite</h2>
        <button className="button" onClick={createBackup}>
          Создать backup
        </button>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Файл</th>
                <th>Размер</th>
                <th>Создан</th>
              </tr>
            </thead>
            <tbody>
              {backups.map((backup) => (
                <tr key={backup.path}>
                  <td>{backup.filename}</td>
                  <td>{formatBytes(backup.size_bytes)}</td>
                  <td>{backup.created_at}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h2>Пользователи</h2>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>ID</th>
                <th>Логин</th>
                <th>Email</th>
                <th>Роль</th>
                <th>Тариф</th>
                <th>Лимит</th>
                <th>Stream key</th>
                <th>Активен</th>
                <th>Действия</th>
              </tr>
            </thead>
            <tbody>
              {users.map((item) => {
                const active = Boolean(item.is_active);
                return (
                  <tr key={item.id}>
                    <td>{item.id}</td>
                    <td>{item.username}</td>
                    <td>{item.email}</td>
                    <td>{item.role}</td>
                    <td>{item.plan || "free"}</td>
                    <td>{item.max_destinations ?? 1}</td>
                    <td>{item.stream_key}</td>
                    <td>{active ? "Да" : "Нет"}</td>
                    <td>
                      <div className="actions">
                        <button className="button secondary" onClick={() => toggleUser(item.id, !active)}>
                          {active ? "Заблокировать" : "Разблокировать"}
                        </button>
                        <button className="button secondary" onClick={() => resetPassword(item.id)}>
                          Пароль
                        </button>
                        <button className="button secondary" onClick={() => updatePlan(item.id)}>
                          Тариф
                        </button>
                        <button className="button secondary" onClick={() => resetStreamKey(item.id)}>
                          Stream key
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h2>Входящие потоки SRS</h2>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Stream key</th>
                <th>Published</th>
                <th>Площадок</th>
                <th>FFmpeg</th>
              </tr>
            </thead>
            <tbody>
              {publishers.map((publisher) => (
                <tr key={publisher.stream_key}>
                  <td>{publisher.stream_key}</td>
                  <td>{publisher.published_at || "-"}</td>
                  <td>{publisher.destinations ?? 0}</td>
                  <td>{publisher.ffmpeg_started ? "Да" : "Нет"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h2>FFmpeg процессы</h2>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Stream key</th>
                <th>PID</th>
                <th>Status</th>
                <th>Bitrate</th>
                <th>FPS</th>
                <th>Resolution</th>
                <th>Действия</th>
              </tr>
            </thead>
            <tbody>
              {streams.map((stream) => (
                <tr key={stream.stream_key}>
                  <td>{stream.stream_key}</td>
                  <td>{stream.pid ?? "-"}</td>
                  <td>{stream.status ?? "-"}</td>
                  <td>{stream.bitrate || "-"}</td>
                  <td>{stream.fps ?? "-"}</td>
                  <td>{stream.resolution || "-"}</td>
                  <td>
                    <div className="actions">
                      <button className="button danger" onClick={() => stopStream(stream.stream_key)}>
                        Stop
                      </button>
                      <button className="button secondary" onClick={() => showLogs(stream.stream_key)}>
                        Logs
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <h3>Недавние завершения</h3>
        <div className="table-wrap">
          <table className="table">
            <tbody>
              {recent.map((stream) => (
                <tr key={`${stream.stream_key}-${stream.started_at}`}>
                  <td>{stream.stream_key}</td>
                  <td>{stream.return_code ?? "-"}</td>
                  <td>{stream.started_at || "-"}</td>
                  <td>
                    <button className="button secondary" onClick={() => showLogs(stream.stream_key)}>
                      Logs
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {logLines.length > 0 && <pre className="log">{logLines.join("\n")}</pre>}
      </div>
    </div>
  );
}

export default function Home() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getMe()
      .then((response) => {
        setUser(response.user);
      })
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []);

  function onAuthenticated(nextUser: User) {
    setUser(nextUser);
  }

  if (loading) {
    return <main className="page">Загрузка...</main>;
  }

  return (
    <main className="page">
      {user?.role === "admin" ? (
        <AdminDashboard
          user={user}
          onLogout={() => {
            setUser(null);
          }}
        />
      ) : user ? (
        <Dashboard
          user={user}
          onUserChange={setUser}
          onLogout={() => {
            setUser(null);
          }}
        />
      ) : (
        <AuthCard onAuthenticated={onAuthenticated} />
      )}
    </main>
  );
}
