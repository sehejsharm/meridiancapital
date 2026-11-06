// Every rupee figure to the paisa, as Angel One shows it: a figure rounded to
// whole rupees can never be checked against the Angel app.
const INR = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const INR_PRECISE = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 2,
});

export function money(value: number | null | undefined, precise = false): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return (precise ? INR_PRECISE : INR).format(value);
}

export function signedMoney(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const formatted = money(Math.abs(value));
  if (value > 0) return `+${formatted}`;
  if (value < 0) return `−${formatted}`;
  return formatted;
}

export function percent(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

export function signedPercent(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}${Math.abs(value * 100).toFixed(digits)}%`;
}

export function points(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return value.toLocaleString("en-IN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "—";
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s % 60}s`;
  return `${s}s`;
}

export function minutes(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return duration(value * 60);
}

/** Engine timestamps are naive IST strings; render them as-is, never re-zoned. */
export function istTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const time = iso.split("T")[1] ?? iso;
  return time.slice(0, 8);
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/**
 * The desk's one date format: "06 Nov 2026" — day first, month in words, so it
 * cannot be misread the way 07/10/2026 can. The blotter said "6 Nov 12:14", the
 * equity table and event log "2026-11-06", version history "21 Oct 10:45".
 * Timestamps are naive IST strings and are never re-zoned.
 */
export function dayLabel(iso: string | null | undefined): string {
  if (!iso) return "—";
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return iso;
  return `${m[3]} ${MONTHS[Number(m[2]) - 1] ?? m[2]} ${m[1]}`;
}

/** "06 Nov 2026, 12:14" from "2026-11-06T12:14:05" or "2026-11-06 12:14"; the date alone if there is no time. */
export function dateTimeLabel(iso: string | null | undefined): string {
  if (!iso) return "—";
  const m = /^(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2}))?/.exec(iso);
  if (!m) return iso;
  return m[2] ? `${dayLabel(m[1])}, ${m[2]}` : dayLabel(m[1]);
}

/** Kept for its callers: the standard date and time. */
export function istDateTime(iso: string | null | undefined): string {
  return dateTimeLabel(iso);
}

/** A trade time, in the standard format. */
export function tradeTime(ts: string | null | undefined): string {
  return dateTimeLabel(ts);
}

export function secondsSince(iso: string | null | undefined, nowIso?: string): number | null {
  if (!iso) return null;
  const parse = (s: string) => Date.parse(`${s.replace(" ", "T")}Z`);
  const then = parse(iso);
  const now = nowIso ? parse(nowIso) : Date.now();
  if (!Number.isFinite(then) || !Number.isFinite(now)) return null;
  return (now - then) / 1000;
}

export function pnlClass(value: number | null | undefined): string {
  if (value === null || value === undefined || value === 0) return "text-ink-secondary";
  return value > 0 ? "text-profit" : "text-loss";
}

export function clamp01(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.min(1, Math.max(0, value));
}
