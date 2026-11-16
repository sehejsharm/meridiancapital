export interface OutlookDay { day: string; weekday: string; tradeable: boolean; expiry: string | null; dte: number | null; nearest_dte: number[]; reason: string | null }
export interface ExpiryOutlook {
  available: boolean; reason?: string; name?: string; min_dte?: number; max_dte?: number;
  today?: OutlookDay | null; days?: OutlookDay[];
}

export interface ExpiryNotice { tone: "blocked" | "ok"; title: string; detail: string; week: { label: string; text: string; ok: boolean }[] }

/** What the desk says about today's contract, and the days ahead. Null when nothing is known. */
export function expiryNotice(o: ExpiryOutlook | null): ExpiryNotice | null {
  if (!o?.available || !o.days?.length) return null;
  const window = `${o.min_dte} to ${o.max_dte} days`;
  const week = o.days.map((d) => ({
    label: d.weekday.slice(0, 3),
    ok: d.tradeable,
    text: d.tradeable ? `${d.dte} day${d.dte === 1 ? "" : "s"} to expiry` : `no trade (${d.nearest_dte.join(" and ")} days)`,
  }));
  const t = o.today;
  if (!t) {
    const next = o.days[0];
    return { tone: "ok", title: "The market is closed today",
      detail: `Next session, ${next.weekday}: ${next.tradeable ? `a contract ${next.dte} days from expiry` : `no trade, ${next.reason}`}.`, week };
  }
  if (!t.tradeable) {
    const next = o.days.find((d) => d.tradeable);
    return {
      tone: "blocked",
      title: `No trade today: no contract is ${window} from expiry`,
      detail: `Today ${t.reason}.${next ? ` The next day it can trade is ${next.weekday} (${next.dte} days to expiry).` : ""}`,
      week,
    };
  }
  return { tone: "ok", title: `Today's contract is ${t.dte} day${t.dte === 1 ? "" : "s"} from expiry`,
    detail: `Inside the program's ${window} window (expiry ${t.expiry}).`, week };
}
