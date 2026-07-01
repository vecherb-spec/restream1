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
