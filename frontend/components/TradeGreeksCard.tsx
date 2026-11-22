import { Card, Empty } from "@/components/ui";
import { pnlClass, signedMoney } from "@/lib/format";
import type { TradeGreeks, TradeGreeksAt } from "@/lib/types";

const num = (v: number | null | undefined, d = 2) => (v == null || !Number.isFinite(v) ? "—" : v.toFixed(d));

function Column({ label, g }: { label: string; g: TradeGreeksAt | null }) {
  return (
    <div className="min-w-0">
      <h3 className="text-2xs font-semibold uppercase tracking-[0.14em] text-ink-secondary">{label}</h3>
      {!g ? (
        <p className="mt-2 text-xs text-ink-muted">Not known: no NIFTY level was recorded at that moment.</p>
      ) : (
        <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-2 text-xs">
          <div><dt className="text-ink-muted">Delta</dt><dd className="font-semibold tabular-nums text-ink">{num(g.delta, 3)}</dd></div>
          <div><dt className="text-ink-muted">Position delta</dt><dd className="tabular-nums text-ink">{num(g.delta_position, 1)} <span className="text-ink-muted">₹/pt</span></dd></div>
          <div><dt className="text-ink-muted">Gamma</dt><dd className="tabular-nums text-ink">{num(g.gamma, 5)}</dd></div>
          <div><dt className="text-ink-muted">Theta / day</dt><dd className="tabular-nums text-ink">{num(g.theta)} <span className="text-ink-muted">({signedMoney(g.theta_position)})</span></dd></div>
          <div><dt className="text-ink-muted">Vega / 1% IV</dt><dd className="tabular-nums text-ink">{num(g.vega)}</dd></div>
          <div><dt className="text-ink-muted">Implied volatility</dt><dd className="tabular-nums text-ink">{num(g.iv, 1)}%</dd></div>
          <div><dt className="text-ink-muted">NIFTY</dt><dd className="tabular-nums text-ink">{num(g.spot, 2)}</dd></div>
          <div><dt className="text-ink-muted">Days to expiry</dt><dd className="tabular-nums text-ink">{g.dte}</dd></div>
        </dl>
      )}
    </div>
  );
}

/** The contract's Greeks when bought and when sold, and what moved the result. */
export function TradeGreeksCard({ greeks }: { greeks: TradeGreeks | undefined }) {
  if (!greeks) {
    return <Card title="Greeks"><Empty>Not on this server yet. Update the server to switch it on.</Empty></Card>;
  }
  const s = greeks.split;
  const rows: [string, number | null, string][] = s
    ? [
        ["NIFTY's move", s.nifty_move, `${s.nifty_points > 0 ? "+" : ""}${s.nifty_points.toFixed(2)} points × average delta`],
        ["Time decay", s.time_decay, "average theta × time held"],
        ["Volatility", s.volatility, `${s.iv_change > 0 ? "+" : ""}${s.iv_change.toFixed(2)} IV points × average vega`],
        ["The rest", s.rest, "gamma and what a first-order split leaves out"],
      ]
    : [];
  return (
    <Card title="Greeks" subtitle="Black-Scholes from the trade's own premium and the NIFTY level at each end">
      <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
        <Column label="When bought" g={greeks.entry} />
        <Column label="When sold" g={greeks.exit} />
      </div>
      {s && (
        <div className="mt-5">
          <h3 className="text-2xs font-semibold uppercase tracking-[0.14em] text-ink-secondary">What made the result (before charges)</h3>
          <dl className="mt-2 divide-y divide-hairline text-xs">
            {rows.map(([label, v, how]) => (
              <div key={label} className="flex items-baseline justify-between gap-3 py-1.5">
                <dt className="min-w-0">
                  <span className="text-ink">{label}</span>
                  <span className="ml-2 text-2xs text-ink-muted">{how}</span>
                </dt>
                <dd className={`shrink-0 font-semibold tabular-nums ${pnlClass(v)}`}>{signedMoney(v)}</dd>
              </div>
            ))}
            <div className="flex items-baseline justify-between gap-3 py-1.5">
              <dt className="font-semibold text-ink">Gross result</dt>
              <dd className={`font-semibold tabular-nums ${pnlClass(s.gross)}`}>{signedMoney(s.gross)}</dd>
            </div>
          </dl>
          <p className="mt-2 text-2xs text-ink-muted">An approximation from the two ends of the trade, not a record of each minute.</p>
        </div>
      )}
    </Card>
  );
}
