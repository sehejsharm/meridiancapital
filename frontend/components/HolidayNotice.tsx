"use client";

import { closureNotice } from "@/lib/holidays";
import { useLiveFeed } from "@/lib/LiveContext";

/** The deck's notice that the market is shut today, or will be at the next weekday. */
export function HolidayNotice() {
  const { status } = useLiveFeed();
  const schedule = status?.schedule;
  const notice = closureNotice(schedule?.closures, schedule?.now_ist);
  if (!notice) return null;
  const today = notice.tone === "today";
  return (
    <div
      role="status"
      className={`flex items-start gap-3 rounded-lg border px-4 py-3 text-xs ${
        today ? "border-warning/50 bg-warning/10 text-warning" : "border-brand/40 bg-brand-dim text-ink"
      }`}
    >
      <CalendarIcon className={`mt-0.5 h-4 w-4 shrink-0 ${today ? "text-warning" : "text-brand"}`} />
      <div className="min-w-0">
        <p className="font-semibold">{notice.title}</p>
        <p className={`mt-0.5 ${today ? "text-warning/90" : "text-ink-secondary"}`}>{notice.detail}</p>
      </div>
    </div>
  );
}

function CalendarIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} aria-hidden="true" className={className}>
      <rect x="3.5" y="5" width="17" height="15" rx="2" />
      <path d="M3.5 9.5h17M8 3v4M16 3v4M9 14l6 4M15 14l-6 4" strokeLinecap="round" />
    </svg>
  );
}
