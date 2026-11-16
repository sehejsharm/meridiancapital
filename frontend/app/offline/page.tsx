"use client";

import { useEffect, useState } from "react";

import { Logo } from "@/components/Logo";
import { Badge, Button } from "@/components/ui";
import { money, pnlClass, signedMoney } from "@/lib/format";
import { LAST_FIGURES_KEY, ageText, parseLastFigures, type LastFigures } from "@/lib/phone";

/**
 * Shown by the service worker when a page cannot be fetched at all. The figures
 * are the last ones this phone saw and are labelled as old: nothing here is live.
 */
export default function OfflinePage() {
  const [figures, setFigures] = useState<LastFigures | null>(null);
  const [now, setNow] = useState<number | null>(null);

  useEffect(() => {
    try {
      setFigures(parseLastFigures(window.localStorage.getItem(LAST_FIGURES_KEY)));
    } catch {
      /* storage unavailable: the page still explains itself */
    }
    setNow(Date.now());
    const tick = window.setInterval(() => setNow(Date.now()), 30_000);
    const back = () => window.location.replace("/today");
    window.addEventListener("online", back);
    return () => {
      window.clearInterval(tick);
      window.removeEventListener("online", back);
    };
  }, []);

  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center gap-6 px-5 py-10">
      <Logo />
      <div>
        <h1 className="text-lg font-semibold text-ink">No connection</h1>
        <p className="mt-2 text-sm leading-relaxed text-ink-secondary">
          This phone cannot reach the desk. The trading program runs on the server and does not need this phone: an open trade keeps
          its stop at Angel. To act without the dashboard, use the Angel One app.
        </p>
      </div>
      <section className="rounded-lg border border-hairline bg-surface p-4">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-2xs font-semibold uppercase tracking-[0.14em] text-ink-secondary">Last figures seen</h2>
          <Badge tone="warning">Old</Badge>
        </div>
        {figures && now !== null ? (
          <>
            <dl className="mt-3 grid grid-cols-2 gap-3 text-xs">
              <div><dt className="text-ink-muted">Day P&amp;L</dt><dd className={`text-base font-semibold tabular-nums ${pnlClass(figures.day_pl)}`}>{signedMoney(figures.day_pl)}</dd></div>
              <div><dt className="text-ink-muted">Equity</dt><dd className="text-base font-semibold tabular-nums text-ink">{money(figures.equity)}</dd></div>
              <div className="col-span-2"><dt className="text-ink-muted">Position</dt>
                <dd className="break-words text-sm font-medium text-ink">
                  {figures.position ? `${figures.position}${figures.unrealised === null ? "" : ` (${signedMoney(figures.unrealised)} then)`}` : "none open"}
                </dd></div>
            </dl>
            <p className="mt-3 text-2xs leading-relaxed text-ink-muted">Seen {ageText(figures.ts, now)}. Anything may have changed since.</p>
          </>
        ) : (
          <p className="mt-3 text-xs text-ink-muted">Nothing has been saved on this phone yet.</p>
        )}
      </section>
      <Button variant="primary" full onClick={() => window.location.replace("/today")}>Try again</Button>
    </main>
  );
}
