import type { AngelMoney, Position } from "@/lib/types";
import { money, signedMoney } from "@/lib/format";
import { Card } from "@/components/ui";

/**
 * The Angel app's Trading Balance Summary, line for line, from the figures the
 * program reads from Angel itself — so every rupee and paisa on the desk can be
 * ticked off against the app.
 */
export function AngelBalance({
  m,
  recorded = false,
  position = null,
}: {
  m: AngelMoney;
  recorded?: boolean;
  /** The trade held now: what was paid for it is out of the balance until it sells. */
  position?: Position | null;
}) {
  const paid = position ? Math.round(position.entry_premium * position.qty * 100) / 100 : null;
  const rows: { label: string; value: string; hint?: string; strong?: boolean; tone?: string }[] = [
    { label: "Opening ledger balance", value: money(m.opening_balance) },
    ...(m.added != null && Math.abs(m.added) >= 0.005
      ? [{
          label: m.added > 0 ? "Funds added" : "Funds withdrawn",
          value: signedMoney(m.added),
          hint: "In Angel's trading balance, not yet in its ledger — paid in or out today (or collateral)",
          tone: "text-ink",
        }]
      : []),
    {
      label: "Premium received",
      value: signedMoney(m.realised_gross),
      hint: "P&L before charges on trades closed today, from Angel's position book",
      tone: (m.realised_gross ?? 0) >= 0 ? "text-profit" : "text-loss",
    },
    ...(paid != null && position
      ? [{
          label: "Premium paid, open trade",
          value: `−${money(paid)}`,
          hint: `${position.side} ${position.strike} × ${position.qty} at ${position.entry_premium.toFixed(2)}, back in the balance when it sells`,
          tone: "text-ink",
        }]
      : []),
    {
      label: m.charges_estimated ? "Charges (buy leg estimated until it sells)" : "Charges taken",
      value: m.charges != null ? `−${money(Math.abs(m.charges))}` : "—",
      hint: m.charges_estimated ? undefined : "What the balance went down by: opening + premium − balance",
      tone: "text-loss",
    },
    { label: "Trading balance", value: money(m.balance), strong: true },
  ];
  const rms = Object.entries(m.rms ?? {}).filter(([, v]) => typeof v === "number" && Number.isFinite(v));
  const open = m.day_gross != null && m.realised_gross != null && Math.abs(m.day_gross - m.realised_gross) >= 0.005;
  return (
    <Card
      title="Angel One balance"
      subtitle={
        recorded
          ? "Last recorded figures — the same lines as the Angel app's Trading Balance Summary"
          : "The same lines as the Angel app's Trading Balance Summary, read from Angel"
      }
    >
      <dl className="divide-y divide-hairline">
        {rows.map((r) => (
          <div key={r.label} className="flex items-baseline justify-between gap-4 py-2.5">
            <dt className="min-w-0 text-xs text-ink-secondary">
              {r.label}
              {r.hint && <span className="mt-0.5 block text-2xs text-ink-muted">{r.hint}</span>}
            </dt>
            <dd
              className={`tnum shrink-0 text-right ${r.strong ? "text-base font-semibold text-ink" : "text-sm"} ${
                r.tone ?? "text-ink"
              }`}
            >
              {r.value}
            </dd>
          </div>
        ))}
      </dl>
      {!m.charges_estimated && m.charges != null && (
        <p className="mt-3 border-t border-hairline pt-2.5 text-2xs leading-relaxed text-ink-muted">
          The Angel app&apos;s &ldquo;Estimated charges&rdquo; line can read a paisa lower. Angel&apos;s charges run to
          fractions of a paisa and that line rounds them down, while its trading balance carries the full amount. The
          balance is the figure that matches to the paisa.
        </p>
      )}
      {rms.length > 0 && (
        <details className="mt-2 text-2xs text-ink-muted">
          <summary className="cursor-pointer select-none py-1 hover:text-ink-secondary">
            Raw figures from Angel (RMS limits)
          </summary>
          <dl className="mt-1 grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-1">
            {rms.map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="truncate">{k}</dt>
                <dd className="tnum text-right text-ink-secondary">{money(v)}</dd>
              </div>
            ))}
          </dl>
        </details>
      )}
      {open && (
        <p className="mt-3 border-t border-hairline pt-2.5 text-2xs leading-relaxed text-ink-muted">
          A trade is open: the desk&apos;s equity is this balance plus the open option at market (
          {signedMoney((m.day_gross ?? 0) - (m.realised_gross ?? 0))} on it so far). The balance itself moves when it
          sells.
        </p>
      )}
    </Card>
  );
}
