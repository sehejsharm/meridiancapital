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

/**
 * Raised when the deployment is misconfigured, as opposed to the control plane
 * being down. The two need different messages: one is a setting to fix here,
 * the other is a machine to go and look at.
 */
export class ConfigError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ConfigError";
  }
}

export function apiBase(): string {
  const base = process.env.MERIDIAN_API_URL?.trim();
  if (!base) {
    throw new ConfigError(
      "MERIDIAN_API_URL is not set on this deployment — add it in Vercel " +
        "(Settings, Environment Variables) pointing at your control plane, " +
        "then redeploy.",
    );
  }
  if (!/^https?:\/\//i.test(base)) {
    throw new ConfigError(
      `MERIDIAN_API_URL is "${base}", which has no scheme — it must start with https://`,
    );
  }
  // Every relayed call carries the operator's bearer token, and the WebSocket
  // URL built from this carries a ticket. Over plain http both would cross the
  // internet readable. Loopback is the one exception — local development,
  // where the traffic never leaves the machine.
  if (/^http:\/\//i.test(base) && !isLoopback(base)) {
    throw new ConfigError(
      `MERIDIAN_API_URL is "${base}" — it must use https://. Plain http would send ` +
        "the session token to the control plane unencrypted.",
    );
  }
  return base.replace(/\/$/, "");
}

function isLoopback(url: string): boolean {
  try {
    const host = new URL(url).hostname;
    return host === "localhost" || host === "127.0.0.1" || host === "[::1]" || host === "::1";
  } catch {
    return false;
  }
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

/**
 * Tells the control plane who is actually signing in.
 *
 * Every dashboard sign-in reaches the control plane from Vercel's servers, so
 * without this the lockout is keyed on Vercel's address — shared by everyone —
 * and five wrong guesses from any bot on the internet lock the operator out.
 * Vercel overwrites x-forwarded-for with the real client address, so it cannot
 * be spoofed from a browser; the control plane only believes the forwarded
 * value when the shared relay secret matches, so it cannot be spoofed by a
 * script calling the API directly either. Without the secret configured on
 * both sides this sends nothing and the old behaviour stands.
 */
export function clientIpHeaders(request: Request): Record<string, string> {
  const secret = process.env.MERIDIAN_RELAY_SECRET?.trim();
  const ip = request.headers.get("x-forwarded-for")?.split(",")[0]?.trim();
  if (!secret || !ip) return {};
  return { "X-Meridian-Relay": secret, "X-Meridian-Client-IP": ip };
}

/** Unauthenticated — used by the login route only. */
export async function login(
  password: string,
  extraHeaders: Record<string, string> = {},
): Promise<{ token: string; expires_in: number }> {
  const res = await fetch(`${apiBase()}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...extraHeaders },
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
