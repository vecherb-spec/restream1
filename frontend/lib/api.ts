export type User = {
  id: number;
  username: string;
  email: string;
  role: "client" | "admin";
  stream_key: string;
  is_active: number | boolean;
  yt_active: number | boolean;
  yt_key: string;
  vk_active: number | boolean;
  vk_url: string;
  vk_key: string;
  rt_active: number | boolean;
  rt_url: string;
  rt_key: string;
  tg_active: number | boolean;
  tg_url: string;
  tg_key: string;
  custom_active: number | boolean;
  custom_url: string;
  custom_key: string;
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
  destinations: number;
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
  progress?: string;
};

export type StreamPublisher = {
  stream_key: string;
  published_at?: string;
  destinations?: number;
  ffmpeg_started?: boolean;
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

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") || "http://localhost:8000";

export const OBS_SERVER_URL =
  process.env.NEXT_PUBLIC_OBS_SERVER_URL || "rtmp://restream.medialive.ru/live";

export const HLS_BASE_URL =
  process.env.NEXT_PUBLIC_HLS_BASE_URL?.replace(/\/$/, "") ||
  "https://restream.medialive.ru/srs/live";

export function tokenStorage() {
  if (typeof window === "undefined") {
    return null;
  }
  return window.localStorage;
}

export function getToken() {
  return tokenStorage()?.getItem("restream_token") || "";
}

export function setToken(token: string) {
  tokenStorage()?.setItem("restream_token", token);
}

export function clearToken() {
  tokenStorage()?.removeItem("restream_token");
}

async function request<T>(
  path: string,
  options: RequestInit = {},
  token?: string,
): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set("Content-Type", "application/json");
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || payload.message || `HTTP ${response.status}`);
  }
  return payload as T;
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

export async function logout(token: string) {
  return request<{ code: number }>("/api/auth/logout", { method: "POST" }, token);
}

export async function getMe(token: string) {
  return request<{ code: number; user: User }>("/api/me", {}, token);
}

export async function getSettings(token: string) {
  return request<{ code: number; user: User }>("/api/me/settings", {}, token);
}

export async function updateSettings(token: string, settings: RestreamSettings) {
  return request<{ code: number; user: User }>(
    "/api/me/settings",
    {
      method: "PUT",
      body: JSON.stringify(settings),
    },
    token,
  );
}

export async function getStreamStatus(token: string) {
  return request<StreamStatus>("/api/me/stream-status", {}, token);
}

export async function getAdminUsers(token: string) {
  return request<{ code: number; users: User[] }>("/api/admin/users", {}, token);
}

export async function setAdminUserActive(token: string, userId: number, isActive: boolean) {
  return request<{ code: number; user: User }>(
    `/api/admin/users/${userId}/active`,
    {
      method: "PATCH",
      body: JSON.stringify({ is_active: isActive }),
    },
    token,
  );
}

export async function resetAdminUserPassword(token: string, userId: number, newPassword: string) {
  return request<{ code: number; message: string }>(
    `/api/admin/users/${userId}/password`,
    {
      method: "POST",
      body: JSON.stringify({ new_password: newPassword }),
    },
    token,
  );
}

export async function resetAdminUserStreamKey(token: string, userId: number) {
  return request<{ code: number; message: string; stream_key: string }>(
    `/api/admin/users/${userId}/stream-key`,
    { method: "POST" },
    token,
  );
}

export async function getAdminStreams(token: string) {
  return request<{
    code: number;
    streams: StreamProcess[];
    publishers: StreamPublisher[];
    recent: StreamProcess[];
  }>("/api/admin/streams", {}, token);
}

export async function stopAdminStream(token: string, streamKey: string) {
  return request<{ code: number; stream_key: string; stopped: boolean }>(
    `/api/admin/streams/${streamKey}/stop`,
    { method: "POST" },
    token,
  );
}

export async function getAdminStreamLogs(token: string, streamKey: string) {
  return request<{ code: number; lines: string[] }>(
    `/api/admin/streams/${streamKey}/logs`,
    {},
    token,
  );
}

export async function getAdminSystemMetrics(token: string) {
  return request<SystemMetrics>("/api/admin/system-metrics", {}, token);
}

export async function getAdminBackups(token: string) {
  return request<{ code: number; backups: BackupInfo[] }>("/api/admin/backups", {}, token);
}

export async function createAdminBackup(token: string) {
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
