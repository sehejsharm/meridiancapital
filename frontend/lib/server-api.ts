/**
 * Server-only access to the Oracle control plane.
 *
 * The browser never learns the API origin or holds a readable token: the session
 * lives sealed in an httpOnly cookie (lib/session-cookie.ts), and every call is
 * relayed through a Next route handler that opens it and attaches the token
 * server-side, together with the relay secret the control plane requires.
 */

import "server-only";
import { createHmac } from "node:crypto";
import { cookies } from "next/headers";
import type { NextRequest, NextResponse } from "next/server";

import { deviceLabel } from "./request-guard";
import {
  deviceCookieName,
  deviceCookieOptions,
  newDeviceSecret,
  openSession,
  sealSession,
  sessionCookieName,
  sessionCookieOptions,
  staleSessionCookieNames,
  validDeviceSecret,
} from "./session-cookie";

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

/** The control plane's token from this request's session cookie, if it is genuine and current. */
export async function sessionToken(): Promise<string | null> {
  const store = await cookies();
  return openSession(store.get(sessionCookieName())?.value);
}

/**
 * Proof that a call comes from this relay. With the shared secret set on the
 * control plane, it answers nothing without this. The secret itself never
 * travels: each call carries a signature over the current time, good for a few
 * minutes, so nothing copied out of a proxy log can be replayed later.
 */
export function relayHeaders(): Record<string, string> {
  const secret = process.env.MERIDIAN_RELAY_SECRET?.trim();
  if (!secret) return {};
  return { "X-Meridian-Relay": relayToken(secret) };
}

/** Must match app/security.py relay_token on the control plane. */
export function relayToken(secret: string, nowMs: number = Date.now()): string {
  const ts = String(Math.floor(nowMs / 1000));
  const sig = createHmac("sha256", secret).update(`meridian-relay-v1:${ts}`).digest("base64url");
  return `v1.${ts}.${sig}`;
}

/**
 * Tells the control plane who is actually signing in.
 *
 * Every dashboard call reaches the control plane from Vercel's servers, so
 * without this the lockout is keyed on Vercel's address — shared by everyone —
 * and the audit log names Vercel instead of the visitor. Vercel overwrites
 * x-forwarded-for with the real client address, so it cannot be spoofed from a
 * browser; the control plane only believes the forwarded value alongside the
 * relay secret, so it cannot be spoofed by a script calling the API directly.
 */
export function clientIpHeaders(request: Request): Record<string, string> {
  const relay = relayHeaders();
  const ip = request.headers.get("x-forwarded-for")?.split(",")[0]?.trim();
  if (!relay["X-Meridian-Relay"] || !ip) return relay;
  return { ...relay, "X-Meridian-Client-IP": ip.slice(0, 64) };
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

/** A raw call to the control plane with the relay secret attached. */
export async function controlPlane(path: string, init: RequestInit = {}): Promise<Response> {
  return fetch(`${apiBase()}${path}`, {
    ...init,
    headers: { ...relayHeaders(), ...((init.headers as Record<string, string> | undefined) ?? {}) },
    cache: "no-store",
  });
}

interface CallOptions {
  method?: string;
  body?: unknown;
}

export async function callApi<T>(path: string, options: CallOptions = {}): Promise<T> {
  const token = await sessionToken();
  if (!token) throw new ApiError(401, "not signed in");

  const res = await controlPlane(path, {
    method: options.method ?? "GET",
    headers: {
      Authorization: `Bearer ${token}`,
      ...(options.body ? { "Content-Type": "application/json" } : {}),
    },
    body: options.body ? JSON.stringify(options.body) : undefined,
  });

  if (!res.ok) {
    throw new ApiError(res.status, await detailOf(res, res.statusText));
  }
  return (await res.json()) as T;
}

export async function detailOf(res: Response, fallback: string): Promise<string> {
  try {
    const parsed = (await res.json()) as { detail?: unknown };
    if (typeof parsed.detail === "string" && parsed.detail) return parsed.detail;
  } catch {
    /* non-JSON error body */
  }
  return fallback;
}

// ── this browser as a device ────────────────────────────────────────────────
export interface Device {
  secret: string;
  /** Not yet stored in the browser: the response must set the cookie. */
  fresh: boolean;
}

/** This browser's device secret, or a new one to store if it has none. */
export function currentDevice(request: NextRequest): Device {
  const held = request.cookies.get(deviceCookieName())?.value;
  if (validDeviceSecret(held)) return { secret: held, fresh: false };
  return { secret: newDeviceSecret(), fresh: true };
}

export function deviceHeaders(device: Device, request: Request): Record<string, string> {
  return {
    "X-Meridian-Device": device.secret,
    // Percent-encoded: a header cannot carry the "·" safely.
    "X-Meridian-Device-Label": encodeURIComponent(deviceLabel(request.headers.get("user-agent"))),
  };
}

export function keepDevice(response: NextResponse, device: Device): void {
  if (device.fresh) response.cookies.set({ name: deviceCookieName(), value: device.secret, ...deviceCookieOptions() });
}

// ── the session cookie ───────────────────────────────────────────────────────
export async function setSessionCookie(response: NextResponse, token: string, expiresIn: number): Promise<void> {
  response.cookies.set({ name: sessionCookieName(), value: await sealSession(token, expiresIn), ...sessionCookieOptions(expiresIn) });
  for (const stale of staleSessionCookieNames()) clearCookie(response, stale);
}

export function clearSessionCookie(response: NextResponse): void {
  clearCookie(response, sessionCookieName());
  for (const stale of staleSessionCookieNames()) clearCookie(response, stale);
}

function clearCookie(response: NextResponse, name: string): void {
  // A "__Host-" cookie can only be replaced by one that is Secure with Path=/.
  response.cookies.set({ name, value: "", path: "/", maxAge: 0, httpOnly: true, secure: name.startsWith("__Host-") || sessionCookieOptions(0).secure, sameSite: "lax" });
}
