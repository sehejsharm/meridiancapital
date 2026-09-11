/**
 * Server-only access to the Oracle control plane.
 *
 * The browser never learns the API origin or holds a bearer token: the session
 * JWT lives in an httpOnly cookie, and every call is relayed through a Next
 * route handler that attaches it server-side.
 */

import "server-only";
import { cookies } from "next/headers";

export const SESSION_COOKIE = "meridian_session";

export function apiBase(): string {
  const base = process.env.MERIDIAN_API_URL;
  if (!base) {
    throw new Error("MERIDIAN_API_URL is not set — point it at your Oracle Cloud API origin");
  }
  return base.replace(/\/$/, "");
}

export function websocketUrl(ticket: string): string {
  const base = apiBase().replace(/^http/, "ws");
  return `${base}/ws/live?ticket=${encodeURIComponent(ticket)}`;
}

export async function sessionToken(): Promise<string | null> {
  const store = await cookies();
  return store.get(SESSION_COOKIE)?.value ?? null;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

interface CallOptions {
  method?: string;
  body?: unknown;
  /** Seconds to cache; omitted means always fresh, which is the default for live data. */
  revalidate?: number;
}

export async function callApi<T>(path: string, options: CallOptions = {}): Promise<T> {
  const token = await sessionToken();
  if (!token) throw new ApiError(401, "not signed in");

  const res = await fetch(`${apiBase()}${path}`, {
    method: options.method ?? "GET",
    headers: {
      Authorization: `Bearer ${token}`,
      ...(options.body ? { "Content-Type": "application/json" } : {}),
    },
    body: options.body ? JSON.stringify(options.body) : undefined,
    cache: options.revalidate ? undefined : "no-store",
    next: options.revalidate ? { revalidate: options.revalidate } : undefined,
  });

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const parsed = (await res.json()) as { detail?: string };
      if (parsed.detail) detail = parsed.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

/** Unauthenticated — used by the login route only. */
export async function login(password: string): Promise<{ token: string; expires_in: number }> {
  const res = await fetch(`${apiBase()}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
    cache: "no-store",
  });
  if (!res.ok) {
    let detail = "sign-in failed";
    try {
      const parsed = (await res.json()) as { detail?: string };
      if (parsed.detail) detail = parsed.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as { token: string; expires_in: number };
}
