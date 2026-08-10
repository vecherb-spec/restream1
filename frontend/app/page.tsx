"use client";

import { useEffect, useState } from "react";
import { HlsPreview } from "@/components/HlsPreview";
import {
  AdminLiveDashboard,
  BackupInfo,
  DestinationPlatformId,
  DestinationProfile,
  NotificationSettings,
  OBS_SERVER_URL,
  PlatformStatus,
  RestreamSettings,
  StreamProcess,
  StreamPublisher,
  StreamStatus,
  SystemMetrics,
  User,
  applyDestinationProfile,
  changeMyPassword,
  createAdminBackup,
  createDestinationProfile,
  deleteDestinationProfile,
  getAdminBackups,
  getAdminDashboardEventsUrl,
  getAdminStreamLogs,
  getAdminStreams,
  getAdminSystemMetrics,
  getAdminUsers,
  getMe,
  getMyStreamLogs,
  getNotificationSettings,
  getStreamStatus,
  getStreamStatusEventsUrl,
  listDestinationProfiles,
  login,
  logout,
  register,
  requestPasswordReset,
  resetMyStreamKey,
  resetAdminUserPassword,
  resetAdminUserStreamKey,
  resetPassword,
  setAdminUserActive,
  stopAdminStream,
  testTelegramNotification,
  updateAdminUserPlan,
  updateDestinationProfile,
  updateNotificationSettings,
  updateSettings,
  updateStreamTitle,
  userToSettings,
} from "@/lib/api";

const PLATFORM_LABELS: Record<DestinationPlatformId, string> = {
  yt: "YouTube",
  vk: "VK",
  rt: "Rutube",
  tg: "Telegram",
  custom: "Custom RTMP",
};

type AuthMode = "login" | "register" | "forgot" | "reset";
type ClientView = "broadcast" | "profile";
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
  return value == null || value === "" ? "N/A" : value;
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

function getPlatformStatus(status: StreamStatus | null, platformId: PlatformId) {
  return status?.platform_statuses?.find((item) => item.id === platformId);
}

function formatUptime(seconds?: number | null) {
  if (seconds == null || Number.isNaN(seconds)) {
    return "N/A";
  }
  const total = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  return [hours, minutes, secs].map((part) => String(part).padStart(2, "0")).join(":");
}

function formatBitrate(status?: PlatformStatus | null) {
  if (!status) {
    return "N/A";
  }
  if (status.bitrate_kbps == null) {
    return "N/A";
  }
  if (status.bitrate_kbps >= 1000) {
    return `${(status.bitrate_kbps / 1000).toFixed(1)} Mbps`;
  }
  return `${Math.round(status.bitrate_kbps)} kbps`;
}

function formatResolutionFps(status?: PlatformStatus | null) {
  if (!status) {
    return "N/A";
  }
  const resolution =
    status.resolution ||
    (status.width && status.height ? `${status.width}×${status.height}` : null);
  const fps = status.fps == null ? null : `${Number(status.fps).toFixed(status.fps % 1 ? 1 : 0)} FPS`;
  if (!resolution && !fps) {
    return "N/A";
  }
  if (resolution && fps) {
    return `${resolution} • ${fps}`;
  }
  return String(resolution || fps);
}

function formatProgressAge(seconds?: number | null) {
  if (seconds == null) {
    return "N/A";
  }
  if (seconds < 60) {
    return `${seconds} sec ago`;
  }
  return `${formatUptime(seconds)} ago`;
}

function maskSecret(value: string) {
  if (!value) {
    return "";
  }
  if (value.length <= 8) {
    return "••••••••";
  }
  return `${value.slice(0, 4)}••••••••${value.slice(-4)}`;
}

const planPresets = [
  { plan: "free", maxDestinations: 1, title: "Free" },
  { plan: "basic", maxDestinations: 3, title: "Basic" },
  { plan: "pro", maxDestinations: 5, title: "Pro" },
  { plan: "admin", maxDestinations: 99, title: "Admin" },
] as const;

function getEffectiveMaxDestinations(user: User) {
  const preset = planPresets.find((item) => item.plan === (user.plan || "free"));
  const planLimit = preset?.maxDestinations ?? 1;
  const stored = user.max_destinations ?? 1;
  return Math.max(stored, planLimit);
}

function AuthCard({
  onAuthenticated,
  initialResetToken = "",
}: {
  onAuthenticated: (user: User) => void;
  initialResetToken?: string;
}) {
  const [mode, setMode] = useState<AuthMode>(initialResetToken ? "reset" : "login");
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [resetIdentifier, setResetIdentifier] = useState("");
  const [resetToken, setResetToken] = useState(initialResetToken);
  const [resetNewPassword, setResetNewPassword] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    setMessage("");
    setLoading(true);
    try {
      if (mode === "forgot") {
        const response = await requestPasswordReset(resetIdentifier);
        setMessage(response.message);
      } else if (mode === "reset") {
        const response = await resetPassword(resetToken, resetNewPassword);
        setMessage(response.message);
        setPassword("");
        setMode("login");
      } else {
        if (mode === "login") {
          await login(username, password);
        } else {
          await register(username, password, email);
        }
        // Confirm the HttpOnly session cookie actually works before entering the app.
        const me = await getMe();
        onAuthenticated(me.user);
      }
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Ошибка запроса");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="card" style={{ maxWidth: 460, margin: "80px auto" }}>
      <h1>Medialive restream</h1>
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
        {mode === "forgot" ? (
          <label className="field">
            Логин или email
            <input
              value={resetIdentifier}
              onChange={(event) => setResetIdentifier(event.target.value)}
              required
            />
          </label>
        ) : mode === "reset" ? (
          <>
            <label className="field">
              Токен восстановления
              <input value={resetToken} onChange={(event) => setResetToken(event.target.value)} required />
            </label>
            <label className="field">
              Новый пароль
              <input
                type="password"
                value={resetNewPassword}
                onChange={(event) => setResetNewPassword(event.target.value)}
                minLength={8}
                required
              />
            </label>
          </>
        ) : (
          <>
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
                minLength={mode === "register" ? 8 : undefined}
                required
              />
            </label>
          </>
        )}
        {error && <div className="error">{error}</div>}
        {message && <div className="alert">{message}</div>}
        <button className="button" disabled={loading}>
          {loading
            ? "Подождите..."
            : mode === "login"
              ? "Войти"
              : mode === "register"
                ? "Зарегистрироваться"
                : mode === "forgot"
                  ? "Отправить письмо"
                  : "Сохранить новый пароль"}
        </button>
        {mode === "login" && (
          <button className="tab" type="button" onClick={() => setMode("forgot")}>
            Забыли пароль?
          </button>
        )}
        {mode !== "login" && (
          <button className="tab" type="button" onClick={() => setMode("login")}>
            Вернуться ко входу
          </button>
        )}
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
  const [savingCredentials, setSavingCredentials] = useState(false);
  const [profiles, setProfiles] = useState<DestinationProfile[]>([]);
  const [applyingProfileId, setApplyingProfileId] = useState<number | null>(null);
  const enabledDestinations = countEnabledDestinations(settings);
  const maxDestinations = getEffectiveMaxDestinations(user);
  const platformsBusy = savingPlatform !== null || savingCredentials || applyingProfileId !== null;

  useEffect(() => {
    listDestinationProfiles()
      .then((response) => setProfiles(response.profiles))
      .catch(() => undefined);
  }, []);

  function updateText(key: keyof RestreamSettings, value: string) {
    setSettings((current) => ({ ...current, [key]: value }));
  }

  async function togglePlatform(platform: PlatformConfig, nextActive: boolean) {
    if (platformsBusy) {
      return;
    }
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
      setSettings(userToSettings(response.user));
      setMessage(`${platform.title}: ${nextActive ? "Start выполнен" : "Stop выполнен"}.`);
    } catch (requestError) {
      setSettings(previousSettings);
      setError(requestError instanceof Error ? requestError.message : "Не удалось переключить площадку");
    } finally {
      setSavingPlatform(null);
    }
  }

  async function savePlatformCredentials() {
    setError("");
    setMessage("");
    setSavingCredentials(true);
    try {
      const response = await updateSettings(settings);
      onSaved(response.user);
      setMessage("URL и ключи сохранены.");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Не удалось сохранить настройки");
    } finally {
      setSavingCredentials(false);
    }
  }

  async function applyProfileToSlot(platform: PlatformConfig, profileIdValue: string) {
    if (!profileIdValue) {
      return;
    }
    const profileId = Number(profileIdValue);
    if (!Number.isFinite(profileId)) {
      return;
    }
    setError("");
    setMessage("");
    setApplyingProfileId(profileId);
    try {
      const response = await applyDestinationProfile(profileId, false);
      onSaved(response.user);
      setSettings(userToSettings(response.user));
      setMessage(`${platform.title}: профиль применён.`);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Не удалось применить профиль");
    } finally {
      setApplyingProfileId(null);
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
      {enabledDestinations >= maxDestinations && maxDestinations <= 1 && (
        <div className="alert">
          На тарифе «{user.plan || "free"}» одновременно активна {maxDestinations} площадка. Чтобы включить
          другую — нажмите Stop на текущей (например YouTube), затем Start на нужной.
        </div>
      )}

      {platformConfigs.map((platform) => {
        const active = Boolean(settings[platform.activeKey]);
        const configured = isPlatformConfigured(platform, settings);
        const platformStatus = getPlatformStatus(streamStatus, platform.id);
        const live = platformStatus?.state === "live";
        const reconnecting = platformStatus?.state === "reconnecting";
        const stateClass = platformStatus?.color || (active ? "yellow" : "gray");
        const startWouldExceedLimit =
          !active &&
          configured &&
          countEnabledDestinations({ ...settings, [platform.activeKey]: true }) > maxDestinations;
        const disabled = platformsBusy || startWouldExceedLimit;
        return (
          <div
            className={`platform-row ${live ? "live" : reconnecting ? "reconnecting" : active ? "enabled" : ""}`}
            key={platform.id}
          >
            <div className="platform-state">
              <span className={`platform-live-dot ${stateClass}`} />
              <div>
                <strong>{platform.title}</strong>
                <small>
                  {platformStatus?.label ||
                    (active ? "Включена, ждет VideoCoder" : "Остановлена")}
                </small>
              </div>
            </div>
            <div className="platform-metrics">
              <span>{formatResolutionFps(platformStatus)}</span>
              <span>{formatBitrate(platformStatus)}</span>
              <span>Uptime: {formatUptime(platformStatus?.uptime_seconds)}</span>
              <span>
                Restarts: {platformStatus?.restart_count ?? 0} • Reconnects:{" "}
                {platformStatus?.reconnect_count ?? 0}
              </span>
              <span>Last progress: {formatProgressAge(platformStatus?.progress_age_seconds)}</span>
              {platformStatus?.worker_pid != null && (
                <span>PID: {platformStatus.worker_pid}</span>
              )}
              {platformStatus?.state === "reconnecting" &&
                platformStatus.next_restart_in_seconds != null && (
                  <span className="platform-next-restart">
                    Next restart: {platformStatus.next_restart_in_seconds} sec
                  </span>
                )}
              {platformStatus?.last_error && (
                <span className="platform-reason">
                  Last error: {platformStatus.last_error}
                  {platformStatus.last_error_at ? ` (${platformStatus.last_error_at})` : ""}
                </span>
              )}
            </div>
            <select
              aria-label={`Профиль ${platform.title}`}
              disabled={platformsBusy}
              value=""
              onChange={(event) => {
                void applyProfileToSlot(platform, event.target.value);
                event.currentTarget.value = "";
              }}
            >
              <option value="">Профиль площадки…</option>
              {profiles
                .filter((profile) => profile.platform_id === platform.id)
                .map((profile) => (
                  <option key={profile.id} value={profile.id}>
                    {profile.name}
                  </option>
                ))}
            </select>
            <input
              placeholder="RTMP URL"
              value={platform.fixedUrl || (platform.urlKey ? String(settings[platform.urlKey]) : "")}
              disabled={Boolean(platform.fixedUrl) || platformsBusy}
              onChange={(event) => {
                if (platform.urlKey) {
                  updateText(platform.urlKey, event.target.value);
                }
              }}
            />
            <input
              placeholder="Stream key"
              value={String(settings[platform.streamKey])}
              disabled={platformsBusy}
              onChange={(event) => updateText(platform.streamKey, event.target.value)}
            />
            <button
              className={`button ${active ? "danger" : "secondary"}`}
              disabled={disabled}
              onClick={() => togglePlatform(platform, !active)}
            >
              {savingPlatform === platform.id ? "..." : active ? "Stop" : "Start"}
            </button>
            {!active && !configured && (
              <small className="platform-reason">Заполните RTMP URL и stream key, затем Start.</small>
            )}
            {startWouldExceedLimit && (
              <small className="platform-reason">
                Лимит {maxDestinations} площ. — сначала Stop на другой включённой площадке.
              </small>
            )}
          </div>
        );
      })}

      {error && <div className="error">{error}</div>}
      {message && <div className="alert">{message}</div>}
      <button className="button secondary" disabled={savingCredentials} onClick={savePlatformCredentials}>
        {savingCredentials ? "Сохранение..." : "Сохранить URL и ключи"}
      </button>
      <p className="muted channel-footnote">
        Start включает площадку в рестрим. Счётчик {enabledDestinations}/{maxDestinations} — сколько
        площадок можно включить одновременно по тарифу.
      </p>
    </aside>
  );
}

function BroadcastTopbar({
  user,
  streamState,
  view,
  onViewChange,
  onLogout,
}: {
  user: User;
  streamState: ReturnType<typeof useStreamStatus>;
  view: ClientView;
  onViewChange: (view: ClientView) => void;
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
            {user.plan || "free"} · до {getEffectiveMaxDestinations(user)} каналов
          </small>
        </div>
        <div className="actions">
          <button
            className={`button ${view === "broadcast" ? "" : "secondary"}`}
            onClick={() => onViewChange("broadcast")}
          >
            Эфир
          </button>
          <button
            className={`button ${view === "profile" ? "" : "secondary"}`}
            onClick={() => onViewChange("profile")}
          >
            Профиль
          </button>
          <button className="button secondary" onClick={onLogout}>
            Выйти
          </button>
        </div>
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
  const [displayTitle, setDisplayTitle] = useState(user.stream_title || "Название трансляции");
  const [draftTitle, setDraftTitle] = useState(user.stream_title || "Название трансляции");
  const [titleError, setTitleError] = useState("");
  const [savingTitle, setSavingTitle] = useState(false);
  const [resettingStreamKey, setResettingStreamKey] = useState(false);
  const [showStreamKey, setShowStreamKey] = useState(false);
  const [clientLogLines, setClientLogLines] = useState<string[]>([]);
  const [clientLogError, setClientLogError] = useState("");
  const [clientLogInfo, setClientLogInfo] = useState("");
  const [loadingClientLogs, setLoadingClientLogs] = useState(false);
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
      setDisplayTitle(nextTitle);
      setDraftTitle(nextTitle);
      onUserChange({ ...response.user, stream_title: nextTitle });
      setEditingTitle(false);
    } catch (requestError) {
      setTitleError(requestError instanceof Error ? requestError.message : "Не удалось сохранить название");
    } finally {
      setSavingTitle(false);
    }
  }

  function cancelTitleEdit() {
    setDraftTitle(displayTitle);
    setTitleError("");
    setEditingTitle(false);
  }

  async function regenerateStreamKey() {
    const confirmed = window.confirm(
      "Сгенерировать новый Stream Key? Старый ключ перестанет работать, текущий эфир будет остановлен. Новый ключ нужно будет вставить в VideoCoder.",
    );
    if (!confirmed) {
      return;
    }

    setResettingStreamKey(true);
    try {
      const response = await resetMyStreamKey();
      const freshAccount = await getMe().catch(() => null);
      const nextUser = freshAccount?.user || response.user || { ...user, stream_key: response.stream_key };
      onUserChange(nextUser);
      setShowStreamKey(true);
      setCopied("new-key");
    } catch (requestError) {
      window.alert(requestError instanceof Error ? requestError.message : "Не удалось сгенерировать ключ");
    } finally {
      setResettingStreamKey(false);
    }
  }

  async function loadClientLogs() {
    setLoadingClientLogs(true);
    setClientLogError("");
    setClientLogInfo("");
    try {
      const response = await getMyStreamLogs(user.stream_key);
      setClientLogLines(response.lines || []);
      if (response.code !== 0 || !response.lines?.length) {
      setClientLogInfo("Лог рестрима пока не создан. Запустите рестрим на площадку, затем попробуйте снова.");
      }
    } catch (requestError) {
      setClientLogLines([]);
      const message = requestError instanceof Error ? requestError.message : "Логи рестрима пока не найдены";
      if (message.toLowerCase().includes("not found") || message.includes("404")) {
        setClientLogInfo("Лог рестрима пока не создан. Запустите рестрим на площадку, затем попробуйте снова.");
      } else {
        setClientLogError(message);
      }
    } finally {
      setLoadingClientLogs(false);
    }
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
                <h1>{displayTitle}</h1>
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
              <p>Добавьте каналы справа, скопируйте RTMP и ключ в VideoCoder, затем начните трансляцию.</p>
              <div className="quick-steps">
                <span>1. Каналы</span>
                <span>2. RTMP + ключ</span>
                <span>3. VideoCoder Start</span>
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
        <div className="broadcast-metric-card">
          <span>Пропуск кадров</span>
          <strong>{status?.dropped_frames == null ? "-" : status.dropped_frames}</strong>
        </div>
      </div>

      <div className="obs-compact-card">
        <div className="obs-row">
          <div>
            <span>VideoCoder Server</span>
            <strong>{OBS_SERVER_URL}</strong>
          </div>
          <button className="button secondary" onClick={() => copyValue("server", OBS_SERVER_URL)}>
            copy
          </button>
        </div>
        <div className="obs-row">
          <div>
            <span>Stream Key</span>
            <strong>{showStreamKey ? user.stream_key : maskSecret(user.stream_key)}</strong>
          </div>
          <div className="obs-actions">
            <button className="button secondary" onClick={() => setShowStreamKey((current) => !current)}>
              {showStreamKey ? "Скрыть" : "Показать"}
            </button>
            <button className="button secondary" onClick={() => copyValue("key", user.stream_key)}>
              copy
            </button>
            <button className="button danger" disabled={resettingStreamKey} onClick={regenerateStreamKey}>
              {resettingStreamKey ? "..." : "Новый ключ"}
            </button>
          </div>
        </div>
        {copied && (
          <small>
            {copied === "new-key" ? "Новый Stream Key сгенерирован. Скопируйте его в VideoCoder." : `Скопировано: ${copied}`}
          </small>
        )}
      </div>

      <div className="client-log-card">
        <div className="client-log-header">
          <div>
            <span className="eyebrow">Диагностика</span>
            <h3>Логи рестрима</h3>
          </div>
          <button className="button secondary" disabled={loadingClientLogs} onClick={loadClientLogs}>
            {loadingClientLogs ? "Загрузка..." : "Показать логи"}
          </button>
        </div>
        {clientLogError && <div className="error">{clientLogError}</div>}
        {clientLogInfo && <div className="alert">{clientLogInfo}</div>}
        {clientLogLines.length > 0 && <pre className="log">{clientLogLines.join("\n")}</pre>}
        {!clientLogError && !clientLogInfo && clientLogLines.length === 0 && (
          <p className="muted">Нажмите кнопку, чтобы посмотреть последние строки FFmpeg-лога.</p>
        )}
      </div>
    </section>
  );
}

function ClientProfile({
  user,
  onUserChange,
}: {
  user: User;
  onUserChange: (user: User) => void;
}) {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [profiles, setProfiles] = useState<DestinationProfile[]>([]);
  const [profilesError, setProfilesError] = useState("");
  const [profilesMessage, setProfilesMessage] = useState("");
  const [profilesBusy, setProfilesBusy] = useState(false);
  const [showAddProfile, setShowAddProfile] = useState(false);
  const [editingProfileId, setEditingProfileId] = useState<number | null>(null);
  const [profileName, setProfileName] = useState("");
  const [profilePlatform, setProfilePlatform] = useState<DestinationPlatformId>("yt");
  const [profileUrl, setProfileUrl] = useState("");
  const [profileKey, setProfileKey] = useState("");
  const [notifySettings, setNotifySettings] = useState<NotificationSettings | null>(null);
  const [notifyEnabled, setNotifyEnabled] = useState(false);
  const [notifyChatId, setNotifyChatId] = useState("");
  const [notifyBusy, setNotifyBusy] = useState(false);
  const [notifyMessage, setNotifyMessage] = useState("");
  const [notifyError, setNotifyError] = useState("");

  async function reloadProfiles() {
    const response = await listDestinationProfiles();
    setProfiles(response.profiles);
  }

  useEffect(() => {
    let active = true;
    listDestinationProfiles()
      .then((response) => {
        if (active) {
          setProfiles(response.profiles);
        }
      })
      .catch((requestError) => {
        if (active) {
          setProfilesError(
            requestError instanceof Error ? requestError.message : "Не удалось загрузить площадки",
          );
        }
      });
    getNotificationSettings()
      .then((settings) => {
        if (!active) {
          return;
        }
        setNotifySettings(settings);
        setNotifyEnabled(Boolean(settings.telegram_enabled));
        setNotifyChatId(settings.telegram_chat_id_set ? settings.telegram_chat_id_masked || "************" : "");
      })
      .catch((requestError) => {
        if (active) {
          setNotifyError(
            requestError instanceof Error ? requestError.message : "Не удалось загрузить уведомления",
          );
        }
      });
    return () => {
      active = false;
    };
  }, []);

  async function submitPassword(event: React.FormEvent) {
    event.preventDefault();
    setMessage("");
    setError("");
    setSaving(true);
    try {
      const response = await changeMyPassword(currentPassword, newPassword);
      setMessage(response.message);
      setCurrentPassword("");
      setNewPassword("");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Не удалось сменить пароль");
    } finally {
      setSaving(false);
    }
  }

  function resetProfileForm() {
    setShowAddProfile(false);
    setEditingProfileId(null);
    setProfileName("");
    setProfilePlatform("yt");
    setProfileUrl("");
    setProfileKey("");
  }

  function startEditProfile(profile: DestinationProfile) {
    setShowAddProfile(true);
    setEditingProfileId(profile.id);
    setProfileName(profile.name);
    setProfilePlatform(profile.platform_id as DestinationPlatformId);
    setProfileUrl(profile.base_url || "");
    setProfileKey(profile.stream_key_masked || "************");
    setProfilesMessage("");
    setProfilesError("");
  }

  async function submitProfile(event: React.FormEvent) {
    event.preventDefault();
    setProfilesBusy(true);
    setProfilesError("");
    setProfilesMessage("");
    try {
      if (editingProfileId != null) {
        await updateDestinationProfile(editingProfileId, {
          name: profileName,
          base_url: profilePlatform === "yt" ? undefined : profileUrl,
          stream_key: profileKey,
        });
        setProfilesMessage("Площадка обновлена.");
      } else {
        await createDestinationProfile({
          name: profileName,
          platform_id: profilePlatform,
          base_url: profilePlatform === "yt" ? "" : profileUrl,
          stream_key: profileKey,
        });
        setProfilesMessage("Площадка добавлена.");
      }
      await reloadProfiles();
      resetProfileForm();
    } catch (requestError) {
      setProfilesError(requestError instanceof Error ? requestError.message : "Не удалось сохранить площадку");
    } finally {
      setProfilesBusy(false);
    }
  }

  async function removeProfile(profile: DestinationProfile) {
    if (!window.confirm(`Удалить площадку «${profile.name}»?`)) {
      return;
    }
    setProfilesBusy(true);
    setProfilesError("");
    setProfilesMessage("");
    try {
      await deleteDestinationProfile(profile.id);
      await reloadProfiles();
      setProfilesMessage("Площадка удалена.");
      if (editingProfileId === profile.id) {
        resetProfileForm();
      }
    } catch (requestError) {
      setProfilesError(requestError instanceof Error ? requestError.message : "Не удалось удалить площадку");
    } finally {
      setProfilesBusy(false);
    }
  }

  async function saveNotifications(event: React.FormEvent) {
    event.preventDefault();
    setNotifyBusy(true);
    setNotifyError("");
    setNotifyMessage("");
    try {
      const payload: { telegram_enabled: boolean; telegram_chat_id?: string } = {
        telegram_enabled: notifyEnabled,
      };
      if (notifyChatId && !/^[*•]+$/.test(notifyChatId)) {
        payload.telegram_chat_id = notifyChatId;
      } else if (notifyChatId) {
        payload.telegram_chat_id = notifyChatId;
      }
      const settings = await updateNotificationSettings(payload);
      setNotifySettings(settings);
      setNotifyEnabled(Boolean(settings.telegram_enabled));
      setNotifyChatId(settings.telegram_chat_id_set ? settings.telegram_chat_id_masked || "************" : "");
      setNotifyMessage(settings.message || "Настройки уведомлений сохранены.");
      onUserChange({
        ...user,
        notify_tg_enabled: settings.telegram_enabled,
        notify_tg_chat_id_masked: settings.telegram_chat_id_masked,
        notify_tg_chat_id_set: settings.telegram_chat_id_set,
        telegram_bot_configured: settings.telegram_bot_configured,
      });
    } catch (requestError) {
      setNotifyError(requestError instanceof Error ? requestError.message : "Не удалось сохранить уведомления");
    } finally {
      setNotifyBusy(false);
    }
  }

  async function checkTelegram() {
    setNotifyBusy(true);
    setNotifyError("");
    setNotifyMessage("");
    try {
      if (notifyChatId && !/^[*•]+$/.test(notifyChatId)) {
        await updateNotificationSettings({
          telegram_enabled: notifyEnabled,
          telegram_chat_id: notifyChatId,
        });
      }
      const settings = await testTelegramNotification();
      setNotifySettings(settings);
      setNotifyMessage(settings.message || "Тестовое сообщение отправлено.");
    } catch (requestError) {
      setNotifyError(requestError instanceof Error ? requestError.message : "Не удалось проверить Telegram");
    } finally {
      setNotifyBusy(false);
    }
  }

  return (
    <section className="profile-page">
      <div className="card">
        <h2>Профиль клиента</h2>
        <div className="profile-grid">
          <div className="metric">
            Логин
            <strong>{user.username}</strong>
          </div>
          <div className="metric">
            Email
            <strong>{user.email || "-"}</strong>
          </div>
          <div className="metric">
            Тариф
            <strong>{user.plan || "free"}</strong>
          </div>
          <div className="metric">
            Площадок
            <strong>до {getEffectiveMaxDestinations(user)}</strong>
          </div>
        </div>
        <p className="muted">
          Текущий тариф определяет, сколько площадок можно включить одновременно.
          Для изменения тарифа обратитесь к администратору.
        </p>
      </div>

      <div className="card">
        <div className="client-log-header">
          <h2>Площадки</h2>
          <button
            className="button secondary"
            type="button"
            disabled={profilesBusy}
            onClick={() => {
              resetProfileForm();
              setShowAddProfile(true);
            }}
          >
            + Добавить площадку
          </button>
        </div>
        <div className="destination-profile-list">
          {profiles.length === 0 && <p className="muted">Сохранённых площадок пока нет.</p>}
          {profiles.map((profile) => (
            <div className="destination-profile-row" key={profile.id}>
              <div>
                <strong>{profile.name}</strong>
                <small>{PLATFORM_LABELS[profile.platform_id as DestinationPlatformId] || profile.platform_id}</small>
              </div>
              <div className="obs-actions">
                <button className="button secondary" type="button" disabled={profilesBusy} onClick={() => startEditProfile(profile)}>
                  Изменить
                </button>
                <button className="button danger" type="button" disabled={profilesBusy} onClick={() => void removeProfile(profile)}>
                  Удалить
                </button>
              </div>
            </div>
          ))}
        </div>
        {showAddProfile && (
          <form className="form destination-profile-form" onSubmit={submitProfile}>
            <h3>{editingProfileId != null ? "Изменить площадку" : "Новая площадка"}</h3>
            <label className="field">
              Название
              <input value={profileName} onChange={(event) => setProfileName(event.target.value)} required />
            </label>
            <label className="field">
              Тип
              <select
                value={profilePlatform}
                disabled={editingProfileId != null}
                onChange={(event) => setProfilePlatform(event.target.value as DestinationPlatformId)}
              >
                {(Object.keys(PLATFORM_LABELS) as DestinationPlatformId[]).map((id) => (
                  <option key={id} value={id}>
                    {PLATFORM_LABELS[id]}
                  </option>
                ))}
              </select>
            </label>
            {profilePlatform !== "yt" && (
              <label className="field">
                RTMP / endpoint URL
                <input
                  value={profileUrl}
                  onChange={(event) => setProfileUrl(event.target.value)}
                  placeholder="rtmp://..."
                  required
                />
              </label>
            )}
            <label className="field">
              Stream key / secret
              <input
                type="password"
                value={profileKey}
                onChange={(event) => setProfileKey(event.target.value)}
                placeholder={editingProfileId != null ? "************" : "stream key"}
                required={editingProfileId == null}
              />
            </label>
            <div className="obs-actions">
              <button className="button" disabled={profilesBusy}>
                {profilesBusy ? "Сохранение..." : "Сохранить"}
              </button>
              <button className="button secondary" type="button" disabled={profilesBusy} onClick={resetProfileForm}>
                Отмена
              </button>
            </div>
          </form>
        )}
        {profilesError && <div className="error">{profilesError}</div>}
        {profilesMessage && <div className="alert">{profilesMessage}</div>}
      </div>

      <form className="card form" onSubmit={saveNotifications}>
        <h2>Telegram notifications</h2>
        <label className="toggle-row">
          <span>Telegram notifications</span>
          <input
            type="checkbox"
            checked={notifyEnabled}
            onChange={(event) => setNotifyEnabled(event.target.checked)}
          />
          <strong>{notifyEnabled ? "ON" : "OFF"}</strong>
        </label>
        <label className="field">
          Telegram Chat ID
          <input
            type="password"
            value={notifyChatId}
            onChange={(event) => setNotifyChatId(event.target.value)}
            placeholder="********"
            autoComplete="off"
          />
        </label>
        <p className="muted">
          Bot token задаётся только на сервере ({notifySettings?.telegram_bot_configured ? "настроен" : "не настроен"}).
          Секреты в интерфейсе не показываются.
        </p>
        {notifyError && <div className="error">{notifyError}</div>}
        {notifyMessage && <div className="alert">{notifyMessage}</div>}
        <div className="obs-actions">
          <button className="button" disabled={notifyBusy}>
            {notifyBusy ? "Сохранение..." : "Сохранить уведомления"}
          </button>
          <button className="button secondary" type="button" disabled={notifyBusy} onClick={() => void checkTelegram()}>
            Проверить Telegram
          </button>
        </div>
      </form>

      <form className="card form" onSubmit={submitPassword}>
        <h2>Смена пароля</h2>
        <label className="field">
          Текущий пароль
          <input
            type="password"
            value={currentPassword}
            onChange={(event) => setCurrentPassword(event.target.value)}
            required
          />
        </label>
        <label className="field">
          Новый пароль
          <input
            type="password"
            value={newPassword}
            onChange={(event) => setNewPassword(event.target.value)}
            required
            minLength={8}
          />
        </label>
        {error && <div className="error">{error}</div>}
        {message && <div className="alert">{message}</div>}
        <button className="button" disabled={saving}>
          {saving ? "Сохраняем..." : "Сменить пароль"}
        </button>
      </form>
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
  const [view, setView] = useState<ClientView>("broadcast");

  async function handleLogout() {
    await logout().catch(() => undefined);
    onLogout();
  }

  return (
    <div className="broadcast-shell">
      <BroadcastTopbar
        user={user}
        streamState={streamState}
        view={view}
        onViewChange={setView}
        onLogout={handleLogout}
      />
      {view === "profile" ? (
        <ClientProfile user={user} onUserChange={onUserChange} />
      ) : (
        <div className="broadcast-layout">
          <BroadcastMain user={user} streamState={streamState} onUserChange={onUserChange} />
          <SettingsForm user={user} streamStatus={streamState.status} onSaved={onUserChange} />
        </div>
      )}
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
    setError("");
    try {
      await setAdminUserActive(userId, isActive);
      setMessage(isActive ? "Пользователь разблокирован." : "Пользователь заблокирован.");
      await loadStaticAdminData();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Не удалось изменить статус");
    }
  }

  async function resetPassword(userId: number) {
    const newPassword = window.prompt("Новый пароль минимум 8 символов");
    if (!newPassword) {
      return;
    }
    setError("");
    try {
      await resetAdminUserPassword(userId, newPassword);
      setMessage("Пароль обновлен. Сессии пользователя сброшены.");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Не удалось обновить пароль");
    }
  }

  async function applyPlanPreset(userId: number, presetPlan: string) {
    const preset = planPresets.find((item) => item.plan === presetPlan);
    if (!preset) {
      setError("Неизвестный тариф.");
      return;
    }
    setError("");
    try {
      const response = await updateAdminUserPlan(userId, preset.plan, preset.maxDestinations);
      setMessage(response.message || `Тариф обновлен: ${preset.title}.`);
      await loadStaticAdminData();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Не удалось обновить тариф");
    }
  }

  async function resetStreamKey(userId: number) {
    if (!window.confirm("Сбросить stream key пользователя? Старый ключ перестанет работать.")) {
      return;
    }
    setError("");
    try {
      const response = await resetAdminUserStreamKey(userId);
      setMessage(`Новый stream key: ${response.stream_key}`);
      await loadStaticAdminData();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Не удалось сбросить stream key");
    }
  }

  async function stopStream(streamKey: string) {
    setError("");
    try {
      await stopAdminStream(streamKey);
      setMessage("Эфир остановлен.");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Не удалось остановить эфир");
    }
  }

  async function showLogs(streamKey: string) {
    setError("");
    try {
      const response = await getAdminStreamLogs(streamKey);
      setLogLines(response.lines || []);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Не удалось загрузить логи");
    }
  }

  async function createBackup() {
    setError("");
    try {
      const response = await createAdminBackup();
      setMessage(`Backup создан: ${response.backup.filename}`);
      await loadStaticAdminData();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Не удалось создать backup");
    }
  }

  function maskStreamKey(streamKey: string) {
    if (!streamKey) {
      return "—";
    }
    if (streamKey.length <= 10) {
      return "••••••••";
    }
    return `${streamKey.slice(0, 8)}…${streamKey.slice(-4)}`;
  }

  async function copyStreamKey(streamKey: string) {
    setError("");
    try {
      await navigator.clipboard.writeText(streamKey);
      setMessage("Stream key скопирован.");
    } catch {
      setError("Не удалось скопировать stream key.");
    }
  }

  const backupTitle = databaseBackend === "postgres" ? "Backup PostgreSQL" : "Backup SQLite";
  const activeUsersCount = users.filter((item) => Boolean(item.is_active)).length;
  const clientUsersCount = users.filter((item) => item.role === "client").length;
  const activePublishersCount = publishers.length;
  const activeFfmpegCount = streams.length;

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
          <h2>Пользователи</h2>
          <div className="metric">
            Всего
            <strong>{users.length}</strong>
          </div>
          <p className="muted">Клиентов: {clientUsersCount}</p>
        </div>
        <div className="card">
          <h2>Активные</h2>
          <div className="metric">
            Accounts
            <strong>{activeUsersCount}</strong>
          </div>
          <p className="muted">Заблокировано: {Math.max(users.length - activeUsersCount, 0)}</p>
        </div>
        <div className="card">
          <h2>В эфире</h2>
          <div className="metric">
            SRS
            <strong>{activePublishersCount}</strong>
          </div>
          <p className="muted">Входящие публикации сейчас</p>
        </div>
        <div className="card">
          <h2>Рестрим</h2>
          <div className="metric">
            FFmpeg
            <strong>{activeFfmpegCount}</strong>
          </div>
          <p className="muted">Активные процессы рестрима</p>
        </div>
      </div>

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
                    <td>
                      <select
                        className="select"
                        value={item.plan || "free"}
                        onChange={(event) => applyPlanPreset(item.id, event.target.value)}
                      >
                        {planPresets.map((preset) => (
                          <option key={preset.plan} value={preset.plan}>
                            {preset.title} · {preset.maxDestinations}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>{item.max_destinations ?? 1}</td>
                    <td title={item.stream_key}>
                      <div className="actions">
                        <code>{maskStreamKey(item.stream_key)}</code>
                        <button
                          className="button secondary"
                          type="button"
                          onClick={() => copyStreamKey(item.stream_key)}
                        >
                          Copy
                        </button>
                      </div>
                    </td>
                    <td>{active ? "Да" : "Нет"}</td>
                    <td>
                      <div className="actions">
                        <button className="button secondary" onClick={() => toggleUser(item.id, !active)}>
                          {active ? "Заблокировать" : "Разблокировать"}
                        </button>
                        <button className="button secondary" onClick={() => resetPassword(item.id)}>
                          Пароль
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

function readResetTokenFromWindow(): string {
  if (typeof window === "undefined") {
    return "";
  }
  const params = new URLSearchParams(window.location.search);
  return params.get("reset_token") || params.get("token") || "";
}

export default function Home() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [bootError, setBootError] = useState("");
  const [resetToken, setResetToken] = useState(readResetTokenFromWindow);
  const [bootTick, setBootTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const params = new URLSearchParams(window.location.search);
    if (params.has("reset_token") || params.has("token")) {
      params.delete("reset_token");
      params.delete("token");
      const nextQuery = params.toString();
      const nextUrl = `${window.location.pathname}${nextQuery ? `?${nextQuery}` : ""}${window.location.hash}`;
      window.history.replaceState({}, "", nextUrl);
    }

    getMe()
      .then((response) => {
        if (!cancelled) {
          setUser(response.user);
        }
      })
      .catch((error) => {
        if (cancelled) {
          return;
        }
        setUser(null);
        const message = error instanceof Error ? error.message : "";
        // 401/нет сессии — нормальный вход на логин. Таймаут/сеть — показываем ошибку.
        if (message.includes("не отвечает") || message.includes("Failed to fetch")) {
          setBootError(message || "Не удалось связаться с API.");
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [bootTick]);

  function onAuthenticated(nextUser: User) {
    setResetToken("");
    setBootError("");
    setUser(nextUser);
  }

  if (loading) {
    return null;
  }

  if (bootError) {
    return (
      <main className="page">
        <div className="card" style={{ maxWidth: 460, margin: "80px auto" }}>
          <h1>Medialive restream</h1>
          <div className="error">{bootError}</div>
          <p className="muted">
            Страница открылась, но запрос к API не завершился. Часто помогает VPN или другой DNS
            (8.8.8.8).
          </p>
          <button className="button" type="button" onClick={() => setBootTick((value) => value + 1)}>
            Повторить
          </button>
          <button
            className="button secondary"
            type="button"
            onClick={() => {
              setBootError("");
            }}
          >
            Перейти ко входу
          </button>
        </div>
      </main>
    );
  }

  // Password-reset links must open the reset form even if a session cookie exists.
  if (resetToken) {
    return (
      <main className="page">
        <AuthCard onAuthenticated={onAuthenticated} initialResetToken={resetToken} />
      </main>
    );
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
