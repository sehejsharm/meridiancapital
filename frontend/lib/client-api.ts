"use client";

/** Browser-side calls. Everything goes through the Next relay, never direct. */

import { EtagCache } from "./etagCache";

export class ClientApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

// Last answer per GET URL with its ETag: an unchanged poll costs a 304 and returns the same object.
const answers = new EtagCache();

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const isGet = !init?.method || init.method === "GET";
  const etag = isGet ? answers.etag(path) : undefined;
  const res = await fetch(path, {
    cache: "no-store",
    ...init,
    ...(etag ? { headers: { ...(init?.headers as Record<string, string> | undefined), "If-None-Match": etag } } : {}),
  });
  if (res.status === 304 && isGet && answers.has(path)) {
    return answers.data(path) as T;
  }
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
  const data = (await res.json()) as T;
  if (isGet) answers.keep(path, res.headers.get("etag"), data);
  return data;
}

/**
 * Builds the relay URL for a control-plane path.
 *
 * The relay re-adds the control plane's own /api prefix, so a caller writing
 * the endpoint out in full — "/api/news" rather than "/news" — would reach
 * /api/api/news and get a 404 that reads on the deck as an outage. Both
 * spellings resolve to the same endpoint so that mistake cannot ship silently.
 */
function relayUrl(path: string): string {
  return `/api/proxy${path.replace(/^\/api(?=\/)/, "")}`;
}

export function apiGet<T>(path: string): Promise<T> {
  return request<T>(relayUrl(path));
}

export function apiPost<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(relayUrl(path), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
}

export function apiDelete<T>(path: string): Promise<T> {
  return request<T>(relayUrl(path), { method: "DELETE" });
}

export async function signOut(): Promise<void> {
  answers.clear();
  await fetch("/api/session", { method: "DELETE" });
  window.location.href = "/login";
}
