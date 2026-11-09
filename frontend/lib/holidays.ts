import type { Closure, Closures, SpecialSession } from "@/lib/types";

const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "Fri 02 Oct" (with the year, "Fri 02 Oct 2026") for an ISO date. Read as a calendar day, never shifted by time zone. */
export function calendarDay(iso: string | null | undefined, withYear = false): string {
  if (!iso) return "—";
  const d = new Date(`${iso.slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return iso;
  const text = `${DAYS[d.getUTCDay()]} ${String(d.getUTCDate()).padStart(2, "0")} ${MONTHS[d.getUTCMonth()]}`;
  return withYear ? `${text} ${d.getUTCFullYear()}` : text;
}

function addDays(iso: string, n: number): string {
  const d = new Date(`${iso.slice(0, 10)}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

function names(list: Closure[]): string {
  return list.map((c) => c.label).join(", ");
}

export interface ClosureNotice {
  tone: "today" | "ahead";
  title: string;
  detail: string;
}

/**
 * What the deck says about the market being shut.
 *
 * On a holiday: closed today. On the last day before one — Friday's included,
 * for a Monday holiday, and the weekend between — closed tomorrow, or closed
 * on that day.
 */
export function closureNotice(closures: Closures | undefined, todayIso: string | undefined): ClosureNotice | null {
  if (!closures || !todayIso) return null;
  const today = todayIso.slice(0, 10);
  const next = closures.next_session
    ? `Next session ${calendarDay(closures.next_session)}, 09:15 IST.`
    : "";
  if (closures.today) {
    return {
      tone: "today",
      title: `Market closed today — ${closures.today.label}`,
      detail: `NSE is shut, so armed algorithms stay off today. ${next}`.trim(),
    };
  }
  if (closures.ahead.length) {
    const first = closures.ahead[0].day;
    const when =
      first === addDays(today, 1)
        ? `tomorrow, ${closures.ahead.map((c) => calendarDay(c.day)).join(" and ")}`
        : closures.ahead.map((c) => calendarDay(c.day)).join(" and ");
    return {
      tone: "ahead",
      title: `Market closed ${when} — ${names(closures.ahead)}`,
      detail: `NSE will be shut, so armed algorithms stay off ${
        closures.ahead.length > 1 ? "those days" : "that day"
      }. ${next}`.trim(),
    };
  }
  return null;
}

/**
 * The deck's notice for a session NSE holds outside its calendar (Muhurat
 * trading): on that day nothing starts on its own, so the operator is told to
 * start what they want traded, and until when it runs.
 */
export function specialNotice(
  schedule:
    | { now_ist: string; special_today?: (SpecialSession & { runs_from: string; stops_at: string }) | null; in_special_window?: boolean; special_upcoming?: SpecialSession[] }
    | undefined,
): { tone: "today" | "ahead"; title: string; detail: string } | null {
  if (!schedule) return null;
  const now = schedule.now_ist.slice(11, 16);
  const t = schedule.special_today;
  if (t) {
    if (now >= t.stops_at) return null; // over for today
    const span = `${t.open}–${t.close} IST`;
    return schedule.in_special_window
      ? {
          tone: "today",
          title: `${t.label} now, ${span}`,
          detail: `What you start keeps running until ${t.stops_at}, then stops. To change which algorithm trades, use Switch on its page.`,
        }
      : {
          tone: "today",
          title: `${t.label} today, ${span}`,
          detail: `Nothing starts on its own today. Start the algorithm you want trading from ${t.runs_from}; it runs until ${t.stops_at}. Its own entry window still decides whether it trades.`,
        };
  }
  const next = (schedule.special_upcoming ?? []).find((s) => s.day > schedule.now_ist.slice(0, 10));
  if (!next) return null;
  return {
    tone: "ahead",
    title: `${next.label} on ${calendarDay(next.day)}, ${next.open}–${next.close} IST`,
    detail: "A special session: nothing starts on its own that day. Start the algorithm you want trading from 30 minutes before the open.",
  };
}
