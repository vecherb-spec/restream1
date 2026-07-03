"use client";

import { useEffect, useState } from "react";
import { HlsPreview } from "@/components/HlsPreview";
import {
  AdminLiveDashboard,
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
  getAdminDashboardEventsUrl,
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
  updateStreamTitle,
  userToSettings,
} from "@/lib/api";

type AuthMode = "login" | "register";
type StreamTransport = "sse" | "fallback";
type PlatformId = "yt" | "vk" | "rt" | "tg" | "custom";

type PlatformConfig = {
  id: PlatformId;
  title: string;
  activeKey: keyof RestreamSettings;
  streamKey: keyof RestreamSettings;
  urlKey?: keyof RestreamSettings;
  fixedUrl?: string;
};

const platformConfigs: PlatformConfig[] = [
  {
    id: "yt",
    title: "YouTube",
    activeKey: "yt_active",
    streamKey: "yt_key",
    fixedUrl: "rtmp://a.rtmp.youtube.com/live2",
  },
  { id: "vk", title: "VK", activeKey: "vk_active", urlKey: "vk_url", streamKey: "vk_key" },
  { id: "rt", title: "Rutube", activeKey: "rt_active", urlKey: "rt_url", streamKey: "rt_key" },
  { id: "tg", title: "Telegram", activeKey: "tg_active", urlKey: "tg_url", streamKey: "tg_key" },
  {
    id: "custom",
    title: "Custom RTMP",
    activeKey: "custom_active",
    urlKey: "custom_url",
    streamKey: "custom_key",
  },
];

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

function useStreamStatus() {
  const [status, setStatus] = useState<StreamStatus | null>(null);
  const [error, setError] = useState("");
  const [transport, setTransport] = useState<StreamTransport>("sse");

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

  return { status, error, transport };
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

function isPlatformConfigured(platform: PlatformConfig, settings: RestreamSettings) {
  const streamKey = String(settings[platform.streamKey] || "").trim();
  const url = platform.fixedUrl || (platform.urlKey ? String(settings[platform.urlKey] || "").trim() : "");
  return Boolean(streamKey && url);
}

function metricValue(value?: string | number | null) {
  return value == null || value === "" ? "-" : value;
}

function displayStatusLabel(label?: string) {
  if (!label) {
    return "Офлайн";
  }
  if (label === "Поток в SRS, рестрим не запущен") {
    return "Есть поток - рестрим не запущен";
  }
  return label;
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

function SettingsForm({
  user,
  streamStatus,
  onSaved,
}: {
  user: User;
  streamStatus: StreamStatus | null;
  onSaved: (user: User) => void;
}) {
  const [settings, setSettings] = useState<RestreamSettings>(() => userToSettings(user));
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [savingPlatform, setSavingPlatform] = useState<PlatformId | null>(null);
  const enabledDestinations = countEnabledDestinations(settings);
  const maxDestinations = user.max_destinations ?? 1;
  const streamIsOnAir = Boolean(
    streamStatus?.publisher || streamStatus?.process?.status === "running",
  );

  function updateText(key: keyof RestreamSettings, value: string) {
    setSettings((current) => ({ ...current, [key]: value }));
  }

  async function togglePlatform(platform: PlatformConfig, nextActive: boolean) {
    setError("");
    setMessage("");
    const nextSettings = {
      ...settings,
      [platform.activeKey]: nextActive,
    };

    if (nextActive && !isPlatformConfigured(platform, nextSettings)) {
      setError(`${platform.title}: заполните RTMP URL и stream key.`);
      return;
    }

    if (nextActive && !settings[platform.activeKey] && countEnabledDestinations(nextSettings) > maxDestinations) {
      setError(`Тариф разрешает ${maxDestinations} активных площадок.`);
      return;
    }

    const previousSettings = settings;
    setSettings(nextSettings);
    setSavingPlatform(platform.id);
    try {
      const response = await updateSettings(nextSettings);
      onSaved(response.user);
      setMessage(`${platform.title}: ${nextActive ? "Start выполнен" : "Stop выполнен"}.`);
    } catch (requestError) {
      setSettings(previousSettings);
      setError(requestError instanceof Error ? requestError.message : "Не удалось переключить площадку");
    } finally {
      setSavingPlatform(null);
    }
  }

  return (
    <aside className="channels-panel">
      <div className="channels-tabs">
        <button className="channel-tab active">Каналы</button>
        <button className="channel-tab">Чат</button>
      </div>
      <div className="channel-search">Поиск</div>
      <div className="channels-toolbar">
        <span>Платформы</span>
        <strong>
          {enabledDestinations}/{maxDestinations}
        </strong>
      </div>

      {platformConfigs.map((platform) => {
        const active = Boolean(settings[platform.activeKey]);
        const configured = isPlatformConfigured(platform, settings);
        const live = active && streamIsOnAir;
        const startWouldExceedLimit =
          !active &&
          configured &&
          countEnabledDestinations({ ...settings, [platform.activeKey]: true }) > maxDestinations;
        const disabled = savingPlatform === platform.id || startWouldExceedLimit;
        return (
          <div className={`platform-row ${live ? "live" : active ? "enabled" : ""}`} key={platform.id}>
            <div className="platform-state">
              <span className={`platform-live-dot ${live ? "live" : active ? "enabled" : ""}`} />
              <div>
                <strong>{platform.title}</strong>
                <small>{live ? "В эфире" : active ? "Включена, ждет OBS" : "Остановлена"}</small>
              </div>
            </div>
            <input
              placeholder="RTMP URL"
              value={platform.fixedUrl || (platform.urlKey ? String(settings[platform.urlKey]) : "")}
              disabled={Boolean(platform.fixedUrl)}
              onChange={(event) => {
                if (platform.urlKey) {
                  updateText(platform.urlKey, event.target.value);
                }
              }}
            />
            <input
              placeholder="Stream key"
              value={String(settings[platform.streamKey])}
              onChange={(event) => updateText(platform.streamKey, event.target.value)}
            />
            <button
              className={`button ${active ? "danger" : "secondary"}`}
              disabled={disabled}
              onClick={() => togglePlatform(platform, !active)}
            >
              {savingPlatform === platform.id ? "..." : active ? "Stop" : "Start"}
            </button>
          </div>
        );
      })}

      {error && <div className="error">{error}</div>}
      {message && <div className="alert">{message}</div>}
      <p className="muted channel-footnote">
        Поля сохраняются сразу при Start/Stop. Красный индикатор означает, что площадка в эфире.
      </p>
    </aside>
  );
}

function BroadcastTopbar({
  user,
  streamState,
  onLogout,
}: {
  user: User;
  streamState: ReturnType<typeof useStreamStatus>;
  onLogout: () => void;
}) {
  const { status } = streamState;
  const statusLabel = displayStatusLabel(status?.label);

  return (
    <header className="broadcast-topbar">
      <div className="broadcast-brand">
        <span className="brand-mark">M</span>
        <strong>MediaLive</strong>
      </div>
      <div className="broadcast-status-pill">
        <Lamp color={status?.color || "red"} />
        <span>{statusLabel}</span>
      </div>
      <div className="broadcast-user">
        <div className="avatar">{user.username.slice(0, 1).toUpperCase()}</div>
        <div>
          <strong>{user.username}</strong>
          <small>
            {user.plan || "free"} · до {user.max_destinations ?? 1} каналов
          </small>
        </div>
        <button className="button secondary" onClick={onLogout}>
          Выйти
        </button>
      </div>
    </header>
  );
}

function BroadcastMain({
  user,
  streamState,
  onUserChange,
}: {
  user: User;
  streamState: ReturnType<typeof useStreamStatus>;
  onUserChange: (user: User) => void;
}) {
  const { status, transport } = streamState;
  const [copied, setCopied] = useState("");
  const [editingTitle, setEditingTitle] = useState(false);
  const [draftTitle, setDraftTitle] = useState(user.stream_title || "Название трансляции");
  const [titleError, setTitleError] = useState("");
  const [savingTitle, setSavingTitle] = useState(false);
  const isOnAir = Boolean(status?.publisher || status?.process?.status === "running");
  const statusLabel = displayStatusLabel(status?.label);

  async function copyValue(label: string, value: string) {
    await navigator.clipboard?.writeText(value);
    setCopied(label);
    window.setTimeout(() => setCopied(""), 1800);
  }

  async function saveTitle() {
    const nextTitle = draftTitle.trim();
    if (!nextTitle) {
      setTitleError("Название не может быть пустым.");
      return;
    }

    setSavingTitle(true);
    setTitleError("");
    try {
      const response = await updateStreamTitle(nextTitle);
      onUserChange(response.user);
      setEditingTitle(false);
    } catch (requestError) {
      setTitleError(requestError instanceof Error ? requestError.message : "Не удалось сохранить название");
    } finally {
      setSavingTitle(false);
    }
  }

  function cancelTitleEdit() {
    setDraftTitle(user.stream_title || "Название трансляции");
    setTitleError("");
    setEditingTitle(false);
  }

  return (
    <section className="broadcast-main">
      <div className="broadcast-preview-card">
        <div className="broadcast-preview-header">
          <div>
            <span className="eyebrow">Главная трансляция</span>
            {editingTitle ? (
              <div className="title-editor">
                <input
                  value={draftTitle}
                  maxLength={120}
                  autoFocus
                  onChange={(event) => setDraftTitle(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault();
                      saveTitle();
                    }
                    if (event.key === "Escape") {
                      cancelTitleEdit();
                    }
                  }}
                />
                <button className="icon-button" disabled={savingTitle} onClick={saveTitle}>
                  ✓
                </button>
                <button className="icon-button" disabled={savingTitle} onClick={cancelTitleEdit}>
                  ×
                </button>
              </div>
            ) : (
              <div className="broadcast-title-row">
                <h1>{user.stream_title || "Название трансляции"}</h1>
                <button
                  className="icon-button"
                  aria-label="Редактировать название трансляции"
                  onClick={() => setEditingTitle(true)}
                >
                  ✎
                </button>
              </div>
            )}
            {titleError && <small className="title-error">{titleError}</small>}
          </div>
          <div className="transport-chip">{transport === "sse" ? "Live" : "fallback"}</div>
        </div>
        <div className={`broadcast-preview ${isOnAir ? "on-air" : ""}`}>
          {isOnAir ? (
            <HlsPreview streamKey={user.stream_key} />
          ) : (
            <div className="broadcast-empty">
              <div className="empty-orb">LIVE</div>
              <h2>Готово к запуску</h2>
              <p>Добавьте каналы справа, скопируйте RTMP и ключ в OBS, затем начните трансляцию.</p>
              <div className="quick-steps">
                <span>1. Каналы</span>
                <span>2. RTMP + ключ</span>
                <span>3. OBS Start</span>
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="broadcast-metrics">
        <div className="broadcast-metric-card">
          <span>Статус</span>
          <strong>{statusLabel}</strong>
        </div>
        <div className="broadcast-metric-card">
          <span>Разрешение</span>
          <strong>{metricValue(status?.resolution)}</strong>
        </div>
        <div className="broadcast-metric-card">
          <span>Битрейт</span>
          <strong>{metricValue(status?.bitrate)}</strong>
        </div>
        <div className="broadcast-metric-card">
          <span>FPS</span>
          <strong>{status?.fps == null ? "-" : status.fps.toFixed(1)}</strong>
        </div>
      </div>

      <div className="obs-compact-card">
        <div className="obs-row">
          <div>
            <span>Server</span>
            <strong>{OBS_SERVER_URL}</strong>
          </div>
          <button className="button secondary" onClick={() => copyValue("server", OBS_SERVER_URL)}>
            copy
          </button>
        </div>
        <div className="obs-row">
          <div>
            <span>Stream Key</span>
            <strong>{user.stream_key}</strong>
          </div>
          <button className="button secondary" onClick={() => copyValue("key", user.stream_key)}>
            copy
          </button>
        </div>
        {copied && <small>Скопировано: {copied}</small>}
      </div>
    </section>
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
  const streamState = useStreamStatus();

  async function handleLogout() {
    await logout().catch(() => undefined);
    onLogout();
  }

  return (
    <div className="broadcast-shell">
      <BroadcastTopbar user={user} streamState={streamState} onLogout={handleLogout} />
      <div className="broadcast-layout">
        <BroadcastMain user={user} streamState={streamState} onUserChange={onUserChange} />
        <SettingsForm user={user} streamStatus={streamState.status} onSaved={onUserChange} />
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
  const [databaseBackend, setDatabaseBackend] = useState<"sqlite" | "postgres">("sqlite");
  const [transport, setTransport] = useState<"sse" | "fallback">("sse");
  const [logLines, setLogLines] = useState<string[]>([]);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  function applyLiveDashboard(payload: AdminLiveDashboard) {
    setMetrics(payload.metrics);
    setStreams(payload.streams);
    setPublishers(payload.publishers);
    setRecent(payload.recent);
    setDatabaseBackend(payload.database_backend);
  }

  async function loadStaticAdminData() {
    try {
      const [usersResponse, backupsResponse] = await Promise.all([
        getAdminUsers(),
        getAdminBackups(),
      ]);
      setUsers(usersResponse.users);
      setBackups(backupsResponse.backups);
      setError("");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Ошибка загрузки админки");
    }
  }

  async function loadLiveAdminDataFallback() {
    try {
      const [metricsResponse, streamsResponse] = await Promise.all([
        getAdminSystemMetrics(),
        getAdminStreams(),
      ]);
      setMetrics(metricsResponse);
      setStreams(streamsResponse.streams);
      setPublishers(streamsResponse.publishers);
      setRecent(streamsResponse.recent);
      setError("");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Ошибка загрузки админки");
    }
  }

  useEffect(() => {
    let active = true;
    let fallbackIntervalId: number | undefined;

    const staticLoadId = window.setTimeout(() => {
      loadStaticAdminData().catch(() => undefined);
    }, 0);

    const liveLoadId = window.setTimeout(() => {
      loadLiveAdminDataFallback().catch(() => undefined);
    }, 0);

    const eventSource = new EventSource(getAdminDashboardEventsUrl(), { withCredentials: true });
    eventSource.onmessage = (event) => {
      if (!active) {
        return;
      }
      try {
        applyLiveDashboard(JSON.parse(event.data) as AdminLiveDashboard);
        setTransport("sse");
        setError("");
      } catch {
        setError("Некорректный SSE payload админки");
      }
    };
    eventSource.onerror = () => {
      if (!active) {
        return;
      }
      setTransport("fallback");
      if (!fallbackIntervalId) {
        fallbackIntervalId = window.setInterval(() => {
          loadLiveAdminDataFallback().catch(() => undefined);
        }, 5000);
      }
    };

    return () => {
      active = false;
      window.clearTimeout(staticLoadId);
      window.clearTimeout(liveLoadId);
      eventSource.close();
      if (fallbackIntervalId) {
        window.clearInterval(fallbackIntervalId);
      }
    };
  }, []);

  async function handleLogout() {
    await logout().catch(() => undefined);
    onLogout();
  }

  async function toggleUser(userId: number, isActive: boolean) {
    await setAdminUserActive(userId, isActive);
    setMessage(isActive ? "Пользователь разблокирован." : "Пользователь заблокирован.");
    await loadStaticAdminData();
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
    await loadStaticAdminData();
  }

  async function resetStreamKey(userId: number) {
    if (!window.confirm("Сбросить stream key пользователя? Старый ключ перестанет работать.")) {
      return;
    }
    const response = await resetAdminUserStreamKey(userId);
    setMessage(`Новый stream key: ${response.stream_key}`);
    await loadStaticAdminData();
  }

  async function stopStream(streamKey: string) {
    await stopAdminStream(streamKey);
    setMessage("Эфир остановлен.");
  }

  async function showLogs(streamKey: string) {
    const response = await getAdminStreamLogs(streamKey);
    setLogLines(response.lines || []);
  }

  async function createBackup() {
    const response = await createAdminBackup();
    setMessage(`Backup создан: ${response.backup.filename}`);
    await loadStaticAdminData();
  }

  const backupTitle = databaseBackend === "postgres" ? "Backup PostgreSQL" : "Backup SQLite";

  return (
    <div className="shell">
      <header className="header">
        <div>
          <h1>Админка Restream</h1>
          <p className="muted">
            Вы вошли как {user.username}. Мониторинг:{" "}
            {transport === "sse" ? "Live" : "fallback polling"}.
          </p>
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
        <h2>{backupTitle}</h2>
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
