/** Rupee and percentage formatting, Indian digit grouping throughout. */

export function money(v: number | null | undefined, decimals = 0): string {
  if (v == null || Number.isNaN(v)) return '₹—';
  return (
    '₹' +
    Number(v).toLocaleString('en-IN', {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    })
  );
}

export function signedMoney(v: number | null | undefined, decimals = 0): string {
  if (v == null || Number.isNaN(v)) return '₹—';
  const sign = v >= 0 ? '+' : '−';
  return sign + money(Math.abs(v), decimals);
}

export function pct(v: number | null | undefined, decimals = 2): string {
  if (v == null || Number.isNaN(v)) return '—';
  return (v >= 0 ? '+' : '') + Number(v).toFixed(decimals) + '%';
}

export function num(v: number | null | undefined, decimals = 1): string {
  if (v == null || Number.isNaN(v)) return '—';
  return Number(v).toLocaleString('en-IN', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

/** "2026-08-04T09:52:11.123" -> "09:52:11" */
export const clockOf = (iso?: string): string => (iso ? iso.slice(11, 19) : '');

export const dayOf = (iso?: string): string => (iso ? iso.slice(0, 10) : '');

export function prettyDate(d?: string): string {
  if (!d) return '—';
  const parsed = new Date(d.length <= 10 ? d + 'T00:00:00' : d);
  if (Number.isNaN(parsed.getTime())) return d;
  return parsed.toLocaleDateString('en-GB', {
    weekday: 'short', day: '2-digit', month: 'short', year: 'numeric',
  });
}

export function prettyDateTime(d?: string | null): string {
  if (!d) return '—';
  const parsed = new Date(d);
  if (Number.isNaN(parsed.getTime())) return d;
  return parsed.toLocaleString('en-GB', {
    weekday: 'short', day: '2-digit', month: 'short',
    hour: '2-digit', minute: '2-digit', hour12: false,
  });
}

export function duration(seconds: number): string {
  if (!seconds || seconds < 0) return '—';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

/** Today's date in the exchange's timezone, not the phone's. */
export function istToday(): string {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Kolkata', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(new Date());
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? '01';
  return `${get('year')}-${get('month')}-${get('day')}`;
}

export function istClock(): string {
  return new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit',
    second: '2-digit', hour12: false,
  }).format(new Date());
}
