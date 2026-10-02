import type { Closure, Closures } from "@/lib/types";

const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "Fri 2 Oct" for an ISO date. Read as a calendar day, never shifted by time zone. */
export function calendarDay(iso: string | null | undefined, withYear = false): string {
  if (!iso) return "—";
  const d = new Date(`${iso.slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return iso;
  const text = `${DAYS[d.getUTCDay()]} ${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`;
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
