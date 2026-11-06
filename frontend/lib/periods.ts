/**
 * Periods for the Reports page's downloads: the week or month a tear sheet
 * covers (Monday to Sunday, or the calendar month) and the Indian financial
 * year (April to March) a tax report covers. Matches backend/app/tearsheet.py
 * and backend/app/tax.py.
 */

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const MONTHS_LONG = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

function parts(iso: string): [number, number, number] {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return [y, m, d];
}

/** The Monday of the week containing an ISO date. */
export function weekStart(iso: string): string {
  const [y, m, d] = parts(iso);
  const t = new Date(Date.UTC(y, m - 1, d));
  const back = (t.getUTCDay() + 6) % 7; // Monday = 0
  t.setUTCDate(t.getUTCDate() - back);
  return t.toISOString().slice(0, 10);
}

/** "Week of 2 Nov 2026" or "November 2026": the same label the PDF carries. */
export function periodLabel(period: "week" | "month", iso: string): string {
  if (period === "week") {
    const [y, m, d] = parts(weekStart(iso));
    return `Week of ${d} ${MONTHS[m - 1]} ${y}`;
  }
  const [y, m] = parts(iso);
  return `${MONTHS_LONG[m - 1]} ${y}`;
}

/** "2026-11-06" -> "2026-27". */
export function fyOf(iso: string): string {
  const [y, m] = parts(iso);
  const start = m >= 4 ? y : y - 1;
  return `${start}-${String((start + 1) % 100).padStart(2, "0")}`;
}
