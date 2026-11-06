/** Session replay: one timeline, 09:15 to 15:30 IST, in minutes. Pure, so it is tested. */
export const OPEN_MIN = 9 * 60 + 15;
export const CLOSE_MIN = 15 * 60 + 30;
export const SESSION_MINUTES = CLOSE_MIN - OPEN_MIN; // 375

/** Minutes since 09:15 for a naive IST timestamp ("2026-11-06T12:14:03" or "2026-11-06 12:14"). */
export function minuteOf(ts: string | null | undefined): number | null {
  const m = /[T ](\d{2}):(\d{2})/.exec(ts ?? "");
  if (!m) return null;
  return Number(m[1]) * 60 + Number(m[2]) - OPEN_MIN;
}

/** Minutes since 09:15 for a chart bar (epoch seconds already shifted to IST). */
export function barMinute(t: number): number {
  const d = new Date(t * 1000);
  return d.getUTCHours() * 60 + d.getUTCMinutes() - OPEN_MIN;
}

/** "12:14" for a minute on the timeline. */
export function clock(minute: number): string {
  const total = OPEN_MIN + Math.round(minute);
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}

/** The last value at or before `minute` in a series of {minute, value}. */
export function valueAt(series: { minute: number; value: number | null }[], minute: number): number | null {
  let out: number | null = null;
  for (const p of series) {
    if (p.minute > minute) break;
    if (p.value != null) out = p.value;
  }
  return out;
}

export interface TimedTrade {
  entry: number | null;
  exit: number | null;
}

/** The trades open at `minute`: entered at or before it, not yet exited. */
export function openAt<T extends TimedTrade>(trades: T[], minute: number): T[] {
  return trades.filter((t) => t.entry != null && t.entry <= minute && (t.exit == null || t.exit > minute));
}
