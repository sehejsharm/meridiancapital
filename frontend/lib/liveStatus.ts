/**
 * Whether a status or snapshot from the server says anything new.
 *
 * The server's status carries its own clock (`now_ist`) and each process's
 * uptime, so it differs every second even when nothing has happened, and the
 * hub pushed it every second. Every push replaced the status object at the top
 * of the app and re-rendered every component on the page, once a second, all
 * day. A push is taken when something other than those clocks changed, or when
 * the copy held is older than STATUS_REFRESH_MS (so the date and "next
 * transition" never go stale).
 */
export const VOLATILE_KEYS = new Set(["now_ist", "uptime_sec", "restart_backoff_remaining"]);
export const STATUS_REFRESH_MS = 10_000;

/** The value as JSON with the clock-like fields left out, keys in a fixed order. */
export function stableKey(value: unknown): string {
  return JSON.stringify(value, (key, v) => {
    if (VOLATILE_KEYS.has(key)) return undefined;
    if (v && typeof v === "object" && !Array.isArray(v)) {
      return Object.fromEntries(Object.entries(v as Record<string, unknown>).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)));
    }
    return v;
  });
}

/** Take the new status? `heldKey` is null before the first one. */
export function statusChanged(heldKey: string | null, nextKey: string, heldAt: number, now: number): boolean {
  return heldKey === null || heldKey !== nextKey || now - heldAt >= STATUS_REFRESH_MS;
}
