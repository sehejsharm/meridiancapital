"use client";

import { useEffect, useState } from "react";

import { Badge, Card } from "@/components/ui";
import { apiGet } from "@/lib/client-api";
import { betaWord, channelPosition, trail, trendWord, type DeskMetrics as Metrics } from "@/lib/deskMetrics";
import { money, pnlClass, signedMoney } from "@/lib/format";
import { useLiveFeed } from "@/lib/LiveContext";
import { pollWhileVisible } from "@/lib/pollWhileVisible";

const n = (v: number | null | undefined, d = 2) => (v == null || !Number.isFinite(v) ? "—" : v.toFixed(d));
const pct = (v: number | null | undefined, d = 1, signed = true) =>
  v == null || !Number.isFinite(v) ? "—" : `${signed && v > 0 ? "+" : ""}${(v * 100).toFixed(d)}%`;

function Tile({ label, value, hint, tone }: { label: string; value: string; hint?: string; tone?: string }) {
  return (
    <div className="min-w-0 rounded-md border border-hairline/70 bg-surface-raised/40 px-3 py-2">
      <dt className="truncate text-2xs uppercase tracking-[0.12em] text-ink-muted">{label}</dt>
      <dd className={`mt-0.5 text-base font-semibold tabular-nums ${tone ?? "text-ink"}`}>{value}</dd>
      {hint && <dd className="text-2xs leading-snug text-ink-muted">{hint}</dd>}
    </div>
  );
}

function Group({ title, note, children }: { title: string; note?: string; children: React.ReactNode }) {
  return (
    <section className="min-w-0">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3">
        <h3 className="text-2xs font-semibold uppercase tracking-[0.14em] text-ink-secondary">{title}</h3>
        {note && <span className="text-2xs text-ink-muted">{note}</span>}
      </div>
      <dl className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">{children}</dl>
    </section>
  );
}

/** Delta, the trail, alpha against NIFTY, performance ratios and market gauges, in one panel. */
export function DeskMetrics() {
  const { snapshot } = useLiveFeed();
  const [m, setM] = useState<Metrics | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => pollWhileVisible(() => {
    apiGet<Metrics>("/desk/metrics").then((x) => { setM(x); setFailed(false); }).catch(() => setFailed(true));
  }, 60_000), []);

  const pos = snapshot?.position ?? null;
  const g = pos?.greeks ?? null;
  const plan = pos?.exit_plan ?? null;
  const tr = trail(pos?.live_premium, plan?.stop_price ?? pos?.stop_price, pos?.entry_premium);
  const sig = snapshot?.signal;
  const chanPos = channelPosition(sig?.spot, sig?.channel_low, sig?.channel_high);
  const p = m?.performance;
  const v = m?.vs_nifty;
  const mk = m?.market;

  return (
    <Card
      title="Desk metrics"
      subtitle="The open position's Greeks and trail, the strategy against NIFTY, performance and the market"
      action={failed ? <Badge tone="warning">update the server</Badge> : undefined}
    >
      <div className="space-y-5">
        <Group title="Open position" note={pos ? `${pos.side} ${pos.strike} · ${pos.qty} qty` : "flat"}>
          <Tile label="Delta" value={n(g?.delta, 3)} hint={g?.delta_position != null ? `${n(g.delta_position, 1)} ₹ per NIFTY point` : pos ? "not known" : "no position"} />
          <Tile label="Theta / day" value={signedMoney(g?.theta_position ?? null)} hint={g ? `${n(g.theta)} a unit` : undefined} />
          <Tile label="Gamma · Vega" value={g ? `${n(g.gamma, 4)} · ${n(g.vega)}` : "—"} hint={g ? `IV ${n(g.iv, 1)}%` : undefined} />
          <Tile
            label="Trail stop"
            value={tr ? money(tr.stop, true) : "—"}
            hint={tr ? `${tr.locked >= 0 ? `locks in ${pct(tr.locked)}` : `${pct(tr.locked)} from entry`} · ${pct(tr.room, 1, false)} below the price${plan ? ` · ${plan.stage}` : ""}` : undefined}
            tone={tr && tr.locked > 0 ? "text-profit" : undefined}
          />
        </Group>

        <Group title="Against NIFTY" note={v?.available ? `${v.days} trading days since ${v.since}${v.enough ? "" : " · too few to mean much yet"}` : v?.reason}>
          <Tile label="Alpha (a year)" value={v?.available ? pct(v.alpha_annual) : "—"} hint="return not explained by NIFTY's moves" tone={v?.available ? pnlClass(v.alpha_annual ?? 0) : undefined} />
          <Tile label="Beta" value={v?.available ? n(v.beta, 2) : "—"} hint={v?.available ? betaWord(v.beta) : undefined} />
          <Tile label="Strategy vs NIFTY" value={v?.available ? `${pct(v.strategy_return)} · ${pct(v.nifty_return)}` : "—"} hint={v?.available ? `difference ${pct(v.excess)}` : undefined} />
          <Tile label="Correlation" value={v?.available ? n(v.correlation, 2) : "—"} hint="1 moves in step, 0 unrelated" />
        </Group>

        <Group title="Performance" note={p?.trades ? `${p.trades} real-money trades` : "no real-money trade yet"}>
          <Tile label="Win rate" value={p?.trades ? pct(p.win_rate, 1, false) : "—"} hint={p?.avg_win != null ? `win ${money(p.avg_win)} · loss ${money(p.avg_loss ?? 0)}` : undefined} />
          <Tile label="Profit factor" value={n(p?.profit_factor ?? null, 2)} hint={p?.trades ? `expectancy ${signedMoney(p.expectancy ?? null)} a trade` : undefined} />
          <Tile label="Sharpe · Sortino" value={p?.trades ? `${n(p.sharpe_per_trade ?? null, 2)} · ${n(p.sortino_per_trade ?? null, 2)}` : "—"} hint="per trade" />
          <Tile
            label="Drawdown"
            value={p?.trades ? signedMoney(p.from_best ?? 0) : "—"}
            hint={p?.trades ? `deepest ${signedMoney(p.max_drawdown ?? 0)} · ${p.streak?.count ?? 0} ${p.streak?.kind === "win" ? "win" : "loss"}${(p.streak?.count ?? 0) === 1 ? "" : (p.streak?.kind === "win" ? "s" : "es")} in a row` : undefined}
            tone={p?.from_best ? "text-loss" : undefined}
          />
        </Group>

        <Group title="Market" note={mk?.nifty_as_of ? `daily close ${mk.nifty_as_of}` : undefined}>
          <Tile label="NIFTY trend" value={trendWord(chanPos)} hint={chanPos != null ? `${chanPos}% of the 90-minute channel` : undefined} />
          <Tile label="NIFTY day" value={mk?.nifty_change_pct != null ? `${mk.nifty_change_pct > 0 ? "+" : ""}${mk.nifty_change_pct.toFixed(2)}%` : "—"} hint={mk?.nifty_close ? n(mk.nifty_close, 2) : undefined} tone={mk?.nifty_change_pct != null ? pnlClass(mk.nifty_change_pct) : undefined} />
          <Tile label="India VIX" value={n(mk?.vix ?? null, 2)} hint="expected volatility, a year" />
          <Tile label="PCR · ATM IV" value={mk?.pcr_oi != null || mk?.atm_iv != null ? `${n(mk?.pcr_oi ?? null, 2)} · ${n(mk?.atm_iv ?? null, 1)}%` : "—"} hint={mk?.max_pain ? `max pain ${mk.max_pain}` : "from the option chain"} />
        </Group>
      </div>
    </Card>
  );
}
