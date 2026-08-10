export type User = {
  id: number;
  username: string;
  email: string;
  role: "client" | "admin";
  plan: string;
  max_destinations: number;
  stream_title: string;
  stream_key: string;
  is_active: number | boolean;
  yt_active: number | boolean;
  yt_key: string;
  yt_profile_id?: number | null;
  vk_active: number | boolean;
  vk_url: string;
  vk_key: string;
  vk_profile_id?: number | null;
  rt_active: number | boolean;
  rt_url: string;
  rt_key: string;
  rt_profile_id?: number | null;
  tg_active: number | boolean;
  tg_url: string;
  tg_key: string;
  tg_profile_id?: number | null;
  custom_active: number | boolean;
  custom_url: string;
  custom_key: string;
  custom_profile_id?: number | null;
  notify_tg_enabled?: boolean;
  notify_tg_chat_id_masked?: string;
  notify_tg_chat_id_set?: boolean;
  telegram_bot_configured?: boolean;
};

export type DestinationPlatformId = "yt" | "vk" | "rt" | "tg" | "custom";

export type DestinationProfile = {
  id: number;
  user_id: number;
  name: string;
  platform_id: DestinationPlatformId | string;
  base_url: string;
  has_stream_key: boolean;
  stream_key_masked: string;
  created_at?: string | null;
  updated_at?: string | null;
};

export type NotificationSettings = {
  code?: number;
  message?: string;
  telegram_enabled: boolean;
  telegram_chat_id_masked: string;
  telegram_chat_id_set: boolean;
  telegram_bot_configured: boolean;
};

export type StreamStatus = {
  code: number;
  stream_key: string;
  color: "red" | "yellow" | "green";
  label: string;
  message: string;
  frame: number;
  fps: number | null;
  bitrate: string;
  resolution: string;
  dropped_frames: number | null;
  destinations: number;
  publisher?: StreamPublisher | null;
  process?: StreamProcess | null;
  recent?: StreamProcess | null;
  platform_statuses?: PlatformStatus[];
};

export type PlatformStatus = {
  id: string;
  title: string;
  destination_type?: string;
  active: boolean;
  configured: boolean;
  state:
    | "not_configured"
    | "stopped"
    | "waiting_input"
    | "live"
    | "error"
    | "starting"
    | "reconnecting"
    | "stopping";
  label: string;
  color: "gray" | "red" | "yellow" | "green";
  reason?: string;
  uptime_seconds?: number | null;
  session_uptime_seconds?: number | null;
  worker_pid?: number | null;
  bitrate?: string | null;
  bitrate_kbps?: number | null;
  width?: number | null;
  height?: number | null;
  resolution?: string | null;
  fps?: number | null;
  restart_count?: number;
  reconnect_count?: number;
  last_error?: string | null;
  last_error_at?: string | null;
  last_progress_at?: string | null;
  progress_age_seconds?: number | null;
  next_restart_in_seconds?: number | null;
  worker_state?: string | null;
};

export type RestreamSettings = {
  yt_active: boolean;
  yt_key: string;
  vk_active: boolean;
  vk_url: string;
  vk_key: string;
  rt_active: boolean;
  rt_url: string;
  rt_key: string;
  tg_active: boolean;
  tg_url: string;
  tg_key: string;
  custom_active: boolean;
  custom_url: string;
  custom_key: string;
};

export type StreamProcess = {
  stream_key: string;
  pid?: number;
  status?: string;
  return_code?: number | null;
  started_at?: string;
  destinations?: number;
  log_path?: string;
  frame?: number;
  fps?: number | null;
  bitrate?: string;
  speed?: string;
  resolution?: string;
  dropped_frames?: number | null;
  progress?: string;
};

export type StreamPublisher = {
  stream_key: string;
  published_at?: string;
  destinations?: number;
  ffmpeg_started?: boolean;
  dropped_frames?: number | null;
};

export type BackupInfo = {
  path: string;
  filename: string;
  size_bytes: number;
  created_at: string;
};

export type SystemMetrics = {
  code: number;
  timestamp: string;
  cpu: {
    cores: number;
    load_1: number;
    load_5: number;
    load_15: number;
    load_1_per_core: number;
  };
  memory: {
    total_bytes?: number;
    available_bytes?: number;
    used_bytes?: number;
    used_percent?: number;
  };
  disk: {
    path: string;
    total_bytes: number;
    used_bytes: number;
    free_bytes: number;
    used_percent: number;
  };
  processes: {
    ffmpeg_active: number;
    publishers_active: number;
  };
  services: Record<string, boolean>;
};

export type AdminLiveDashboard = {
  code: number;
  database_backend: "sqlite" | "postgres";
  metrics: SystemMetrics;
  streams: StreamProcess[];
  publishers: StreamPublisher[];
  recent: StreamProcess[];
};

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") || "";

const DEFAULT_REQUEST_TIMEOUT_MS = 10_000;

export const OBS_SERVER_URL =
  process.env.NEXT_PUBLIC_OBS_SERVER_URL || "rtmp://restream.medialive.ru/live";

export const HLS_BASE_URL =
  process.env.NEXT_PUBLIC_HLS_BASE_URL?.replace(/\/$/, "") ||
  "https://restream.medialive.ru/srs/live";

function formatApiErrorDetail(detail: unknown, fallback: string): string {
  if (typeof detail === "string" && detail.trim()) {
    return detail;
  }
  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) => {
        if (typeof item === "string") {
          return item;
        }
        if (item && typeof item === "object" && "msg" in item) {
          return String((item as { msg: unknown }).msg);
        }
        return "";
      })
      .filter(Boolean);
    if (parts.length) {
      return parts.join("; ");
    }
  }
  if (detail && typeof detail === "object" && "message" in detail) {
    return String((detail as { message: unknown }).message);
  }
  return fallback;
}

async function request<T>(
  path: string,
  options: RequestInit = {},
  token?: string,
  timeoutMs: number = DEFAULT_REQUEST_TIMEOUT_MS,
): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set("Content-Type", "application/json");
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  if (options.signal) {
    if (options.signal.aborted) {
      controller.abort();
    } else {
      options.signal.addEventListener("abort", () => controller.abort(), { once: true });
    }
  }

  try {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      ...options,
      headers,
      credentials: "include",
      signal: controller.signal,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      if (response.status === 404 && path.includes("/auth/forgot-password")) {
        throw new Error(
          "Сервис восстановления пароля не обновлён на сервере. Нужно обновить backend.",
        );
      }
      if (response.status === 404 && path.includes("/auth/reset-password")) {
        throw new Error(
          "Сервис сброса пароля не обновлён на сервере. Нужно обновить backend.",
        );
      }
      throw new Error(
        formatApiErrorDetail(
          (payload as { detail?: unknown; message?: unknown }).detail ??
            (payload as { message?: unknown }).message,
          `HTTP ${response.status}`,
        ),
      );
    }
    return payload as T;
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") {
      throw new Error("Сервер не отвечает. Проверьте интернет или VPN и попробуйте снова.");
    }
    throw error;
  } finally {
    clearTimeout(timeoutId);
  }
}

export async function login(username: string, password: string) {
  return request<{ code: number; token: string; user: User }>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
}

export async function register(username: string, password: string, email: string) {
  return request<{ code: number; token: string; user: User }>("/api/auth/register", {
    method: "POST",
    body: JSON.stringify({ username, password, email }),
  });
}

export async function logout(token?: string) {
  return request<{ code: number }>("/api/auth/logout", { method: "POST" }, token);
}

export async function requestPasswordReset(identifier: string) {
  return request<{ code: number; message: string; delivery?: string }>(
    "/api/auth/forgot-password",
    {
      method: "POST",
      body: JSON.stringify({ identifier }),
    },
  );
}

export async function resetPassword(token: string, newPassword: string) {
  return request<{ code: number; message: string }>("/api/auth/reset-password", {
    method: "POST",
    body: JSON.stringify({ token, new_password: newPassword }),
  });
}

export async function getMe(token?: string) {
  return request<{ code: number; user: User }>("/api/me", {}, token);
}

export async function getSettings(token?: string) {
  return request<{ code: number; user: User }>("/api/me/settings", {}, token);
}

export async function updateSettings(settings: RestreamSettings, token?: string) {
  return request<{ code: number; user: User; ffmpeg_started?: boolean }>(
    "/api/me/settings",
    {
      method: "PUT",
      body: JSON.stringify(settings),
    },
    token,
  );
}

export async function updateStreamTitle(streamTitle: string, token?: string) {
  return request<{ code: number; message: string; user: User }>(
    "/api/me/stream-title",
    {
      method: "PUT",
      body: JSON.stringify({ stream_title: streamTitle }),
    },
    token,
  );
}

export async function resetMyStreamKey(token?: string) {
  return request<{ code: number; message: string; stream_key: string; user?: User }>(
    "/api/me/stream-key",
    { method: "POST" },
    token,
  );
}

export async function changeMyPassword(currentPassword: string, newPassword: string, token?: string) {
  return request<{ code: number; message: string }>(
    "/api/me/password",
    {
      method: "POST",
      body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
    },
    token,
  );
}

export async function listDestinationProfiles(token?: string) {
  return request<{ code: number; profiles: DestinationProfile[] }>(
    "/api/me/destination-profiles",
    {},
    token,
  );
}

export async function createDestinationProfile(
  payload: {
    name: string;
    platform_id: DestinationPlatformId | string;
    base_url?: string;
    stream_key: string;
  },
  token?: string,
) {
  return request<{ code: number; message: string; profile: DestinationProfile }>(
    "/api/me/destination-profiles",
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
    token,
  );
}

export async function updateDestinationProfile(
  profileId: number,
  payload: {
    name?: string;
    base_url?: string;
    stream_key?: string;
  },
  token?: string,
) {
  return request<{ code: number; message: string; profile: DestinationProfile }>(
    `/api/me/destination-profiles/${profileId}`,
    {
      method: "PUT",
      body: JSON.stringify(payload),
    },
    token,
  );
}

export async function deleteDestinationProfile(profileId: number, token?: string) {
  return request<{ code: number; message: string }>(
    `/api/me/destination-profiles/${profileId}`,
    { method: "DELETE" },
    token,
  );
}

export async function applyDestinationProfile(
  profileId: number,
  activate = false,
  token?: string,
) {
  return request<{ code: number; message: string; user: User; ffmpeg_started?: boolean }>(
    `/api/me/destination-profiles/${profileId}/apply`,
    {
      method: "POST",
      body: JSON.stringify({ activate }),
    },
    token,
  );
}

export async function getNotificationSettings(token?: string) {
  return request<NotificationSettings>("/api/me/notifications", {}, token);
}

export async function updateNotificationSettings(
  payload: { telegram_enabled?: boolean; telegram_chat_id?: string },
  token?: string,
) {
  return request<NotificationSettings>(
    "/api/me/notifications",
    {
      method: "PUT",
      body: JSON.stringify(payload),
    },
    token,
  );
}

export async function testTelegramNotification(token?: string) {
  return request<NotificationSettings>(
    "/api/me/notifications/telegram/test",
    { method: "POST" },
    token,
  );
}

export async function getStreamStatus(token?: string) {
  return request<StreamStatus>("/api/me/stream-status", {}, token);
}

export async function getMyStreamLogs(streamKey: string, token?: string) {
  return request<{ code: number; message?: string; lines: string[] }>("/api/me/stream-logs", {}, token);
}

export function getStreamStatusEventsUrl() {
  return `${API_BASE_URL}/api/me/stream-status/events`;
}

export function getAdminDashboardEventsUrl() {
  return `${API_BASE_URL}/api/admin/dashboard/events`;
}

export async function getAdminUsers(token?: string) {
  return request<{ code: number; users: User[] }>("/api/admin/users", {}, token);
}

export async function setAdminUserActive(userId: number, isActive: boolean, token?: string) {
  return request<{ code: number; user: User }>(
    `/api/admin/users/${userId}/active`,
    {
      method: "PATCH",
      body: JSON.stringify({ is_active: isActive }),
    },
    token,
  );
}

export async function resetAdminUserPassword(userId: number, newPassword: string, token?: string) {
  return request<{ code: number; message: string }>(
    `/api/admin/users/${userId}/password`,
    {
      method: "POST",
      body: JSON.stringify({ new_password: newPassword }),
    },
    token,
  );
}

export async function updateAdminUserPlan(
  userId: number,
  plan: string,
  maxDestinations: number,
  token?: string,
) {
  return request<{ code: number; message: string; user: User }>(
    `/api/admin/users/${userId}/plan`,
    {
      method: "PATCH",
      body: JSON.stringify({ plan, max_destinations: maxDestinations }),
    },
    token,
  );
}

export async function resetAdminUserStreamKey(userId: number, token?: string) {
  return request<{ code: number; message: string; stream_key: string }>(
    `/api/admin/users/${userId}/stream-key`,
    { method: "POST" },
    token,
  );
}

export async function getAdminStreams(token?: string) {
  return request<{
    code: number;
    streams: StreamProcess[];
    publishers: StreamPublisher[];
    recent: StreamProcess[];
  }>("/api/admin/streams", {}, token);
}

export async function stopAdminStream(streamKey: string, token?: string) {
  return request<{ code: number; stream_key: string; stopped: boolean }>(
    `/api/admin/streams/${streamKey}/stop`,
    { method: "POST" },
    token,
  );
}

export async function getAdminStreamLogs(streamKey: string, token?: string) {
  return request<{ code: number; lines: string[] }>(
    `/api/admin/streams/${streamKey}/logs`,
    {},
    token,
  );
}

export async function getAdminSystemMetrics(token?: string) {
  return request<SystemMetrics>("/api/admin/system-metrics", {}, token);
}

export async function getAdminBackups(token?: string) {
  return request<{ code: number; backups: BackupInfo[] }>("/api/admin/backups", {}, token);
}

export async function createAdminBackup(token?: string) {
  return request<{ code: number; backup: BackupInfo }>(
    "/api/admin/backups",
    { method: "POST" },
    token,
  );
}

export function userToSettings(user: User): RestreamSettings {
  return {
    yt_active: Boolean(user.yt_active),
    yt_key: user.yt_key || "",
    vk_active: Boolean(user.vk_active),
    vk_url: user.vk_url || "",
    vk_key: user.vk_key || "",
    rt_active: Boolean(user.rt_active),
    rt_url: user.rt_url || "",
    rt_key: user.rt_key || "",
    tg_active: Boolean(user.tg_active),
    tg_url: user.tg_url || "",
    tg_key: user.tg_key || "",
    custom_active: Boolean(user.custom_active),
    custom_url: user.custom_url || "",
    custom_key: user.custom_key || "",
  };
}
