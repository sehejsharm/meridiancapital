import "server-only";

/**
 * A first, cheap line in front of the sign-in route.
 *
 * Counts attempts per client address in this function instance's memory and
 * answers 429 before anything reaches the control plane. It is best-effort by
 * nature — serverless instances do not share memory and are recycled — so it
 * is not what stops a determined attacker; the control plane's lockout is. What
 * it does is keep a flood from a single address off the VM entirely, and stop
 * that flood from burning through the lockout budget in seconds.
 */

const WINDOW_MS = 5 * 60 * 1000;
const MAX_ATTEMPTS = 10;
const MAX_TRACKED = 5000;

const hits = new Map<string, number[]>();

export function clientIp(request: Request): string {
  return request.headers.get("x-forwarded-for")?.split(",")[0]?.trim() || "unknown";
}

/** Records an attempt; returns seconds to wait if this one is over the limit. */
export function overLimit(ip: string, now = Date.now()): number | null {
  const recent = (hits.get(ip) ?? []).filter((t) => now - t < WINDOW_MS);
  if (recent.length >= MAX_ATTEMPTS) {
    hits.set(ip, recent);
    return Math.ceil((recent[0] + WINDOW_MS - now) / 1000);
  }
  recent.push(now);
  hits.set(ip, recent);

  // Bound memory: a spray from many addresses must not grow this forever.
  if (hits.size > MAX_TRACKED) {
    for (const [key, times] of hits) {
      if (times.every((t) => now - t >= WINDOW_MS)) hits.delete(key);
      if (hits.size <= MAX_TRACKED * 0.8) break;
    }
  }
  return null;
}
