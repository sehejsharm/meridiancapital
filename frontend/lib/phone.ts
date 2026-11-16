/**
 * What the phone keeps for itself: whether notifications can be switched on here,
 * and the last figures the desk showed, for the offline page to mark as old.
 */

export const LAST_FIGURES_KEY = "meridian.last-figures";

export interface LastFigures {
  ts: string;                 // when the desk last heard from the server (ISO)
  day_pl: number | null;
  equity: number | null;
  position: string | null;    // the contract held, or null when flat
  unrealised: number | null;
}

export type PushSupport = "ready" | "install-first" | "unsupported" | "blocked";

/**
 * Whether this browser can take notifications. An iPhone only allows them for a
 * site added to the home screen, so Safari in a tab answers "install-first".
 */
export function pushSupport(env: { hasServiceWorker: boolean; hasPushManager: boolean; hasNotification: boolean;
  permission: string | null; isIos: boolean; standalone: boolean }): PushSupport {
  if (env.isIos && !env.standalone) return "install-first";
  if (!env.hasServiceWorker || !env.hasPushManager || !env.hasNotification) return "unsupported";
  return env.permission === "denied" ? "blocked" : "ready";
}

/** A base64url public key as the bytes the browser's subscribe() wants. */
export function keyBytes(base64url: string): Uint8Array {
  const b64 = base64url.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (base64url.length % 4)) % 4);
  const raw = atob(b64);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i += 1) out[i] = raw.charCodeAt(i);
  return out;
}

/** "3 min ago", "2 h 5 min ago", "2 days ago": how old the offline page's figures are. */
export function ageText(fromIso: string, nowMs: number): string {
  const then = Date.parse(fromIso);
  if (!Number.isFinite(then)) return "at an unknown time";
  const min = Math.max(0, Math.floor((nowMs - then) / 60_000));
  if (min < 1) return "under a minute ago";
  if (min < 60) return `${min} min ago`;
  const h = Math.floor(min / 60);
  if (h < 24) return `${h} h ${min % 60} min ago`;
  const d = Math.floor(h / 24);
  return `${d} day${d === 1 ? "" : "s"} ago`;
}

export function parseLastFigures(raw: string | null): LastFigures | null {
  if (!raw) return null;
  try {
    const v = JSON.parse(raw) as Partial<LastFigures>;
    return typeof v.ts === "string" ? { ts: v.ts, day_pl: num(v.day_pl), equity: num(v.equity),
      position: typeof v.position === "string" ? v.position : null, unrealised: num(v.unrealised) } : null;
  } catch {
    return null;
  }
}

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}
