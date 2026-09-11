"use client";

/** Browser-side calls. Everything goes through the Next relay, never direct. */

export class ClientApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { cache: "no-store", ...init });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const parsed = (await res.json()) as { detail?: string };
      if (parsed.detail) detail = parsed.detail;
    } catch {
      /* non-JSON error body */
    }
    if (res.status === 401) window.location.href = "/login";
    throw new ClientApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

export function apiGet<T>(path: string): Promise<T> {
  return request<T>(`/api/proxy${path}`);
}

export function apiPost<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(`/api/proxy${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
}

export function apiDelete<T>(path: string): Promise<T> {
  return request<T>(`/api/proxy${path}`, { method: "DELETE" });
}

export async function signOut(): Promise<void> {
  await fetch("/api/session", { method: "DELETE" });
  window.location.href = "/login";
}
