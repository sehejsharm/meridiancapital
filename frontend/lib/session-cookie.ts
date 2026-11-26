/**
 * The session and device cookies: their names, sealing, and reading them back.
 *
 * Imported by proxy.ts, by the route handlers and by tests, so it imports nothing
 * server-only and uses only Web Crypto.
 *
 * The session cookie holds the control plane's token sealed with AES-256-GCM under
 * a key only this deployment has (derived from MERIDIAN_SESSION_SECRET, or else
 * MERIDIAN_RELAY_SECRET). proxy.ts opens it before any page of the desk is served:
 * a made-up, altered or expired cookie is turned away at the door, before a page
 * renders, and the browser never holds the token in readable form. With neither
 * secret set the cookie is the bare token and the door can check only its shape
 * and expiry (the control plane still checks everything); the desk says so.
 *
 * The device cookie is a random secret that identifies this browser to the
 * control plane, which signs in only devices an operator approved.
 */

const enc = new TextEncoder();
const dec = new TextDecoder();
const AAD = enc.encode("meridian-session-v1");

const onVercel = () => process.env.VERCEL === "1";

/** "__Host-" on the real deployment: no other subdomain can set or overwrite it. */
export function sessionCookieName(): string {
  return onVercel() ? "__Host-meridian_session" : "meridian_session";
}

export function deviceCookieName(): string {
  return onVercel() ? "__Host-meridian_device" : "meridian_device";
}

/** Names this deployment no longer reads but a browser may still hold. */
export function staleSessionCookieNames(): string[] {
  return ["meridian_session", "__Host-meridian_session"].filter((n) => n !== sessionCookieName());
}

function secureCookies(): boolean {
  return onVercel() || process.env.NODE_ENV === "production";
}

export function sessionCookieOptions(maxAge: number) {
  return { httpOnly: true, secure: secureCookies(), sameSite: "lax" as const, path: "/", maxAge };
}

// Browsers cap cookie lifetimes at 400 days.
export const DEVICE_COOKIE_MAX_AGE = 400 * 24 * 3600;

export function deviceCookieOptions() {
  return { httpOnly: true, secure: secureCookies(), sameSite: "strict" as const, path: "/", maxAge: DEVICE_COOKIE_MAX_AGE };
}

// ── base64url ────────────────────────────────────────────────────────────────
export function b64u(bytes: Uint8Array): string {
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function unb64u(text: string): Uint8Array {
  const padded = text.replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(padded + "=".repeat((4 - (padded.length % 4)) % 4));
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i += 1) out[i] = raw.charCodeAt(i);
  return out;
}

// ── the device ───────────────────────────────────────────────────────────────
const DEVICE_SECRET_RX = /^[A-Za-z0-9_-]{32,128}$/;

export function validDeviceSecret(value: string | null | undefined): value is string {
  return typeof value === "string" && DEVICE_SECRET_RX.test(value);
}

export function newDeviceSecret(): string {
  return b64u(crypto.getRandomValues(new Uint8Array(32)));
}

// ── sealing ──────────────────────────────────────────────────────────────────
export function sealingConfigured(): boolean {
  return sealingSecret() !== null;
}

function sealingSecret(): string | null {
  const secret = (process.env.MERIDIAN_SESSION_SECRET || process.env.MERIDIAN_RELAY_SECRET || "").trim();
  return secret ? secret : null;
}

let cachedKey: { secret: string; key: Promise<CryptoKey> } | null = null;

function sealingKey(): Promise<CryptoKey> | null {
  const secret = sealingSecret();
  if (!secret) return null;
  if (cachedKey?.secret !== secret) {
    const key = (async () => {
      const base = await crypto.subtle.importKey("raw", enc.encode(secret), "HKDF", false, ["deriveKey"]);
      return crypto.subtle.deriveKey(
        { name: "HKDF", hash: "SHA-256", salt: enc.encode("meridian/session-cookie"), info: enc.encode("v1") },
        base,
        { name: "AES-GCM", length: 256 },
        false,
        ["encrypt", "decrypt"],
      );
    })();
    cachedKey = { secret, key };
  }
  return cachedKey.key;
}

/** The cookie value for a token that expires in `expiresInSec`. */
export async function sealSession(token: string, expiresInSec: number, now: number = Date.now()): Promise<string> {
  const key = sealingKey();
  if (!key) return token;
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const body = enc.encode(JSON.stringify({ t: token, e: Math.floor(now / 1000) + Math.max(0, Math.floor(expiresInSec)) }));
  const sealed = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv, additionalData: AAD }, await key, body));
  return `v1.${b64u(iv)}.${b64u(sealed)}`;
}

/** The token inside a session cookie, or null when it is missing, made up, altered or expired. */
export async function openSession(value: string | null | undefined, now: number = Date.now()): Promise<string | null> {
  if (!value || value.length > 8192) return null;
  const key = sealingKey();
  if (!key) return bareToken(value, now);
  const parts = value.split(".");
  if (parts.length !== 3 || parts[0] !== "v1") return null;
  try {
    const plain = await crypto.subtle.decrypt(
      { name: "AES-GCM", iv: unb64u(parts[1]), additionalData: AAD },
      await key,
      unb64u(parts[2]),
    );
    const { t, e } = JSON.parse(dec.decode(plain)) as { t?: unknown; e?: unknown };
    if (typeof t !== "string" || !t || typeof e !== "number" || e * 1000 <= now) return null;
    return t;
  } catch {
    return null;
  }
}

/**
 * No sealing secret on this deployment: the cookie is the control plane's token
 * itself. Only the control plane can check its signature; here, its shape and expiry.
 */
function bareToken(value: string, now: number): string | null {
  const parts = value.split(".");
  if (parts.length !== 3) return null;
  try {
    const payload = JSON.parse(dec.decode(unb64u(parts[1]))) as { exp?: unknown };
    if (typeof payload.exp !== "number" || payload.exp * 1000 <= now) return null;
    return value;
  } catch {
    return null;
  }
}
