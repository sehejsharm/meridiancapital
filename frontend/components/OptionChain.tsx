"use client";

import { useEffect, useMemo, useState } from "react";

import { Badge, Card, Empty } from "@/components/ui";
import { useLiveFeed } from "@/lib/LiveContext";
import { apiGet } from "@/lib/client-api";
import { oiPct, signedCompact, skewChart } from "@/lib/chain";
import { istTime } from "@/lib/format";
import { calendarDay } from "@/lib/holidays";
import type { ChainAnalytics, ChainPayload, ChainRow, ChainSide, ProgramContract } from "@/lib/types";

const fmt = (v: number | null | undefined, digits = 2) =>
  v === null || v === undefined || !Number.isFinite(v) ? "—" : v.toFixed(digits);

const compact = (v: number | null | undefined) => {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  if (v >= 1e7) return `${(v / 1e7).toFixed(2)}Cr`;
  if (v >= 1e5) return `${(v / 1e5).toFixed(2)}L`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(1)}K`;
  return String(Math.round(v));
};

/**
 * The option chain around the contract being traded.
 *
 * Streams through a running engine's Angel session every 15 seconds. The row
 * and side the account actually holds are highlighted — including a contract
 * a standalone program opened — and its full detail sits above the table.
 * Greeks are derived from each live premium by Black–Scholes.
 */
export function OptionChain() {
  const [chain, setChain] = useState<ChainPayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const c = await apiGet<ChainPayload>("/market/chain");
        if (alive) {
          setChain(c);
          setError(null);
        }
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : "chain unavailable");
      }
    };
    void load();
    const timer = setInterval(() => void load(), 15_000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  const hl = chain?.highlight ?? null;
  const focus = useMemo(() => {
    if (!chain?.rows?.length) return null;
    if (hl?.strike && hl.right) {
      const row = chain.rows.find((r) => r.strike === hl.strike);
      const side = row?.[hl.right === "CE" ? "ce" : "pe"];
      if (side) return { side, strike: hl.strike, right: hl.right, held: true };
    }
    const atm = chain.rows.find((r) => r.strike === chain.atm);
    return atm?.ce ? { side: atm.ce, strike: atm.strike, right: "CE" as const, held: false } : null;
  }, [chain, hl]);

  const subtitle = chain?.available
    ? `NIFTY ${fmt(chain.spot)} · expiry ${chain.expiry} (${chain.dte}d) · lot ${chain.lot}`
    : chain?.program?.held
      ? "The contract your program holds, with its Greeks"
      : chain?.program
        ? "What your program buys if NIFTY breaks out"
        : "Price, volume, open interest and Greeks";

  return (
    <Card
      title="Option chain"
      subtitle={subtitle}
      action={
        chain?.available ? (
          <Badge tone={chain.stale ? "warning" : "good"} dot={!chain.stale}>
            {chain.stale ? "stale" : "live"}
          </Badge>
        ) : undefined
      }
    >
      {error && !chain ? (
        <Empty>Option chain unavailable — {error}</Empty>
      ) : !chain ? (
        <Empty>Loading…</Empty>
      ) : !chain.available && chain.program ? (
        <ProgramPanel program={chain.program} />
      ) : !chain.available ? (
        <Empty>{chain.reason}</Empty>
      ) : (
        <div className="space-y-4">
          {focus && <ContractDetail focus={focus} holding={hl} />}
          {chain.analytics && <ChainSummary a={chain.analytics} spot={chain.spot ?? null} />}

          <div>
            <table className="w-full text-2xs tabular-nums">
              <thead>
                <tr className="border-b border-hairline text-ink-muted">
                  <th className="hidden py-1.5 pr-2 text-right font-medium md:table-cell">OI</th>
                  <th className="hidden py-1.5 pr-2 text-right font-medium md:table-cell">Vol</th>
                  <th className="py-1.5 pr-2 text-right font-medium">IV</th>
                  <th className="py-1.5 pr-2 text-right font-medium">Δ</th>
                  <th className="py-1.5 pr-2 text-right font-medium">Call</th>
                  <th className="px-2 py-1.5 text-center font-semibold text-ink">Strike</th>
                  <th className="py-1.5 pl-2 text-left font-medium">Put</th>
                  <th className="py-1.5 pl-2 text-left font-medium">Δ</th>
                  <th className="py-1.5 pl-2 text-left font-medium">IV</th>
                  <th className="hidden py-1.5 pl-2 text-left font-medium md:table-cell">Vol</th>
                  <th className="hidden py-1.5 pl-2 text-left font-medium md:table-cell">OI</th>
                </tr>
              </thead>
              <tbody>
                {chain.rows!.map((row) => {
                  const isAtm = row.strike === chain.atm;
                  const heldCe = hl?.strike === row.strike && hl?.right === "CE";
                  const heldPe = hl?.strike === row.strike && hl?.right === "PE";
                  const itmCe = (chain.spot ?? 0) > row.strike;
                  const cell = (held: boolean, itm: boolean) =>
                    held ? "bg-brand-dim text-brand font-semibold" : itm ? "bg-surface-raised/60" : "";
                  return (
                    <tr key={row.strike} className="border-b border-hairline/60 last:border-0">
                      <OiCell side={row.ce} a={chain.analytics} align="right" tone={cell(heldCe, itmCe)} />
                      <td className={`hidden py-1.5 pr-2 text-right md:table-cell ${cell(heldCe, itmCe)}`}>{compact(row.ce?.volume)}</td>
                      <td className={`py-1.5 pr-2 text-right ${cell(heldCe, itmCe)}`}>{fmt(row.ce?.iv, 1)}</td>
                      <td className={`py-1.5 pr-2 text-right ${cell(heldCe, itmCe)}`}>{fmt(row.ce?.delta, 2)}</td>
                      <td className={`py-1.5 pr-2 text-right text-ink ${cell(heldCe, itmCe)}`}>{fmt(row.ce?.ltp)}</td>
                      <td className={`px-2 py-1.5 text-center font-semibold ${isAtm ? "text-brand" : "text-ink"}`}>
                        {row.strike}
                      </td>
                      <td className={`py-1.5 pl-2 text-left text-ink ${cell(heldPe, !itmCe)}`}>{fmt(row.pe?.ltp)}</td>
                      <td className={`py-1.5 pl-2 text-left ${cell(heldPe, !itmCe)}`}>{fmt(row.pe?.delta, 2)}</td>
                      <td className={`py-1.5 pl-2 text-left ${cell(heldPe, !itmCe)}`}>{fmt(row.pe?.iv, 1)}</td>
                      <td className={`hidden py-1.5 pl-2 text-left md:table-cell ${cell(heldPe, !itmCe)}`}>{compact(row.pe?.volume)}</td>
                      <OiCell side={row.pe} a={chain.analytics} align="left" tone={cell(heldPe, !itmCe)} />
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <SkewChartPanel rows={chain.rows!} spot={chain.spot ?? null} />
          <p className="text-2xs text-ink-muted">
            Shaded cells are in the money; the gold cell is the contract held. Bars show each strike&apos;s open
            interest against the heaviest one shown
            {chain.analytics?.oi_since ? `; the figure under it is the change since ${istTime(chain.analytics.oi_since)}, the desk's first reading today` : ""}.
            PCR, max pain and the walls are over the {chain.analytics?.strikes_shown ?? chain.rows!.length} strikes shown. {chain.model}.
          </p>
        </div>
      )}
    </Card>
  );
}

function OiCell({ side, a, align, tone }: { side: ChainSide | null; a?: ChainAnalytics; align: "left" | "right"; tone: string }) {
  const pct = oiPct(side?.oi, a?.max_oi);
  const change = side ? a?.oi_change?.[side.token] : undefined;
  return (
    <td className={`relative hidden py-1.5 md:table-cell ${align === "right" ? "pr-2 text-right" : "pl-2 text-left"} ${tone}`}>
      <span
        aria-hidden
        className={`absolute inset-y-1 rounded-sm bg-brand/15 ${align === "right" ? "right-0" : "left-0"}`}
        style={{ width: `${pct}%` }}
      />
      <span className="relative">{compact(side?.oi)}</span>
      {change !== undefined && (
        <span className={`relative block text-[10px] ${change > 0 ? "text-good" : change < 0 ? "text-critical" : "text-ink-muted"}`}>
          {signedCompact(change)}
        </span>
      )}
    </td>
  );
}

function ChainSummary({ a, spot }: { a: ChainAnalytics; spot: number | null }) {
  const away = (k: number | null) => (k != null && spot != null ? `${k >= spot ? "+" : "−"}${Math.abs(k - spot).toFixed(0)} pts` : undefined);
  const items: { label: string; value: string; hint?: string }[] = [
    { label: "PCR (OI)", value: fmt(a.pcr_oi), hint: a.pcr_volume != null ? `volume ${fmt(a.pcr_volume)}` : undefined },
    { label: "Max pain", value: a.max_pain != null ? String(a.max_pain) : "—", hint: away(a.max_pain) },
    { label: "Call wall", value: a.max_call_oi != null ? String(a.max_call_oi) : "—", hint: "most call OI" },
    { label: "Put wall", value: a.max_put_oi != null ? String(a.max_put_oi) : "—", hint: "most put OI" },
    { label: "ATM IV", value: a.atm_iv != null ? `${fmt(a.atm_iv, 1)}%` : "—" },
    {
      label: "Skew",
      value: a.skew ? `${a.skew.skew >= 0 ? "+" : ""}${fmt(a.skew.skew, 1)} pts` : "—",
      hint: a.skew ? `${a.skew.put_strike}P vs ${a.skew.call_strike}C IV` : undefined,
    },
  ];
  return (
    <dl className="grid grid-cols-3 gap-x-4 gap-y-3 rounded-lg border border-hairline px-3 py-3 sm:grid-cols-6">
      {items.map((i) => (
        <div key={i.label} className="min-w-0">
          <dt className="text-2xs uppercase tracking-[0.1em] text-ink-muted">{i.label}</dt>
          <dd className="mt-0.5 truncate text-xs font-semibold tabular-nums text-ink">{i.value}</dd>
          {i.hint && <dd className="truncate text-[10px] text-ink-muted">{i.hint}</dd>}
        </div>
      ))}
    </dl>
  );
}

const SKEW_W = 600;
const SKEW_H = 110;

function SkewChartPanel({ rows, spot }: { rows: ChainRow[]; spot: number | null }) {
  const c = skewChart(rows, SKEW_W, SKEW_H);
  if (!c) return null;
  const k0 = rows[0].strike;
  const k1 = rows[rows.length - 1].strike;
  const spotIn = spot != null && spot >= k0 && spot <= k1;
  return (
    <figure className="space-y-1.5">
      <figcaption className="flex flex-wrap items-center justify-between gap-2 text-2xs text-ink-muted">
        <span className="uppercase tracking-[0.12em]">Implied volatility by strike</span>
        <span className="flex items-center gap-3">
          <span className="inline-flex items-center gap-1.5"><span className="inline-block h-0.5 w-4 bg-brand" />Calls</span>
          <span className="inline-flex items-center gap-1.5"><span className="inline-block h-0.5 w-4 bg-ink-secondary" />Puts</span>
        </span>
      </figcaption>
      <div className="relative">
        <svg viewBox={`0 0 ${SKEW_W} ${SKEW_H}`} preserveAspectRatio="none" className="h-28 w-full" role="img"
          aria-label={`Implied volatility from ${fmt(c.lo, 1)}% to ${fmt(c.hi, 1)}% across strikes ${k0} to ${k1}`}>
          <line x1={0} x2={SKEW_W} y1={SKEW_H - 0.5} y2={SKEW_H - 0.5} stroke="var(--baseline)" vectorEffect="non-scaling-stroke" />
          {spotIn && (
            <line x1={c.x(spot!)} x2={c.x(spot!)} y1={0} y2={SKEW_H} stroke="var(--text-muted)" strokeDasharray="3 3" vectorEffect="non-scaling-stroke" />
          )}
          <path d={c.pe} fill="none" stroke="var(--text-secondary)" strokeWidth={1.4} vectorEffect="non-scaling-stroke" />
          <path d={c.ce} fill="none" stroke="var(--brand)" strokeWidth={1.6} vectorEffect="non-scaling-stroke" />
        </svg>
        <span className="pointer-events-none absolute left-1 top-0 text-[10px] tabular-nums text-ink-muted">{fmt(c.hi, 1)}%</span>
        <span className="pointer-events-none absolute bottom-0.5 left-1 text-[10px] tabular-nums text-ink-muted">{fmt(c.lo, 1)}%</span>
      </div>
      <div className="flex justify-between text-[10px] tabular-nums text-ink-muted">
        <span>{k0}</span>
        {spotIn && <span>spot {fmt(spot, 0)}</span>}
        <span>{k1}</span>
      </div>
    </figure>
  );
}

function ContractDetail({
  focus,
  holding,
}: {
  focus: { side: ChainSide; strike: number; right: "CE" | "PE"; held: boolean };
  holding: ChainPayload["highlight"];
}) {
  const s = focus.side;
  const items: [string, string][] = [
    ["LTP", fmt(s.ltp)],
    ["Change", s.change === null ? "—" : `${s.change >= 0 ? "+" : ""}${fmt(s.change)} (${fmt(s.change_pct)}%)`],
    ["Bid / Ask", `${fmt(s.bid)} / ${fmt(s.ask)}`],
    ["Volume", compact(s.volume)],
    ["Open interest", compact(s.oi)],
    ["IV", s.iv === null ? "—" : `${fmt(s.iv, 1)}%`],
    ["Delta", fmt(s.delta, 3)],
    ["Gamma", fmt(s.gamma, 5)],
    ["Theta / day", fmt(s.theta)],
    ["Vega / 1%", fmt(s.vega)],
  ];
  return (
    <div className="rounded-lg border border-hairline bg-surface-raised p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-semibold text-ink">
          {focus.strike} {focus.right} <span className="text-2xs font-normal text-ink-muted">{s.tsym}</span>
        </p>
        <Badge tone={focus.held ? "brand" : "neutral"}>
          {focus.held
            ? `held · ${holding?.qty ?? "?"} qty${holding?.source === "account" ? " · from Angel" : ""}`
            : "at the money — nothing held"}
        </Badge>
      </div>
      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-5">
        {items.map(([k, v]) => (
          <div key={k} className="min-w-0">
            <dt className="text-2xs uppercase tracking-[0.1em] text-ink-muted">{k}</dt>
            <dd className="mt-0.5 truncate text-xs tabular-nums text-ink">{v}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

// Money to the paisa, like every other figure on the desk.
const rupees = (v: number | null | undefined, digits = 2) =>
  v === null || v === undefined || !Number.isFinite(v)
    ? "—"
    : `${v < 0 ? "−" : ""}₹${Math.abs(v).toLocaleString("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;

function Stat({ label, value, tone, hint }: { label: string; value: string; tone?: string; hint?: string }) {
  return (
    <div className="min-w-0">
      <div className="text-2xs uppercase tracking-[0.12em] text-ink-muted">{label}</div>
      <div className={`text-sm font-semibold tabular-nums ${tone ?? "text-ink"}`}>{value}</div>
      {hint && <div className="text-2xs text-ink-muted">{hint}</div>}
    </div>
  );
}

/**
 * While a standalone program trades, the full chain is not fetched — it would
 * spend the Angel request budget the program trades on. Everything here comes
 * from the program's own status: the contract it holds, with Greeks solved on
 * our server from the premium it already reads, or, while it is flat, the
 * contracts a breakout would buy.
 */
function ProgramPanel({ program }: { program: ProgramContract }) {
  // The price and P&L come from the live feed, the same second as the algorithm
  // card and the balance card; this panel's own read is every 15 s and drew a
  // price a few ticks old beside theirs. The Greeks keep the 15 s read.
  const { snapshot } = useLiveFeed();
  const pos = snapshot?.position;
  const held = program.held;
  const h =
    held && pos && pos.strike === held.strike && pos.side === held.side && pos.live_premium != null
      ? {
          ...held,
          live: pos.live_premium,
          unrealised:
            held.entry != null && held.qty ? Math.round((pos.live_premium - held.entry) * held.qty * 100) / 100 : held.unrealised,
          gain_pct: held.entry ? (pos.live_premium / held.entry - 1) * 100 : held.gain_pct,
        }
      : held;
  if (h) {
    const g = h.greeks;
    const up = (h.gain_pct ?? 0) >= 0;
    return (
      <div className="space-y-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <div className="text-base font-semibold text-ink">
            {h.side === "CE" ? "Call" : "Put"} {h.strike?.toLocaleString("en-IN")}
            <span className="ml-2 text-xs font-normal text-ink-muted">
              {h.tsym} · expires {calendarDay(h.expiry)}{h.dte != null ? ` (${h.dte}d)` : ""} · {h.lots} lot ({h.qty})
            </span>
          </div>
          <Badge tone={up ? "good" : "critical"}>{h.gain_pct != null ? `${up ? "+" : ""}${h.gain_pct.toFixed(1)}%` : "—"}</Badge>
        </div>
        <div className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-4">
          <Stat label="Bought at" value={fmt(h.entry)} />
          <Stat label="Now" value={fmt(h.live)} tone={up ? "text-good" : "text-critical"} />
          <Stat label="Open P&L" value={rupees(h.unrealised)} tone={(h.unrealised ?? 0) >= 0 ? "text-good" : "text-critical"} />
          <Stat label="Best so far" value={h.peak_pct != null ? `${h.peak_pct >= 0 ? "+" : ""}${h.peak_pct.toFixed(1)}%` : "—"} />
          <Stat label="Stop at Angel" value={fmt(h.stop)} hint="sells if the option falls here" />
          {h.target_level != null ? (
            <Stat label="Target" value={`NIFTY ${h.target_level.toLocaleString("en-IN")}`} hint={program.spot != null ? `${Math.abs(h.target_level - program.spot).toFixed(0)} points away` : undefined} />
          ) : (
            <Stat label="Target" value={h.trail_pct != null ? `Trailing ${h.trail_pct}%` : "—"} hint={h.trail_pct != null ? "no fixed target; the stop follows the best price" : undefined} />
          )}
          <Stat label="Implied vol" value={g ? `${g.iv.toFixed(1)}%` : "—"} />
          <Stat label="Delta" value={g ? g.delta.toFixed(2) : "—"} hint={g?.delta_position != null ? `${rupees(Math.abs(g.delta_position))} per NIFTY point` : undefined} />
          <Stat label="Time decay" value={g?.theta_position != null ? `${rupees(g.theta_position)}/day` : "—"} tone="text-critical" hint={g ? `${fmt(g.theta)} per unit per day` : undefined} />
          <Stat label="Vega" value={g ? fmt(g.vega) : "—"} hint="per 1% change in volatility" />
          <Stat label="Gamma" value={g ? g.gamma.toFixed(4) : "—"} />
        </div>
        <p className="text-2xs text-ink-muted">
          From your program&apos;s own prices; Greeks are solved on our server by Black–Scholes. The full chain
          is not fetched while a program trades, so this panel costs no Angel requests.
        </p>
      </div>
    );
  }
  const n = program.next;
  return (
    <div className="space-y-4">
      {n ? (
        <div className="grid gap-3 sm:grid-cols-2">
          {([["call", "Upward breakout", "above", n.call] as const, ["put", "Downward breakout", "below", n.put] as const]).map(
            ([key, title, dir, leg]) => (
              <div key={key} className="rounded-md border border-hairline p-3">
                <div className="text-2xs uppercase tracking-[0.12em] text-ink-muted">{title}</div>
                <div className="mt-1 text-base font-semibold text-ink">
                  Buys the {leg.strike.toLocaleString("en-IN")} {key === "call" ? "call" : "put"}
                </div>
                <div className="mt-0.5 text-2xs text-ink-muted">
                  {leg.trigger != null ? `if a minute closes ${dir} ${leg.trigger.toLocaleString("en-IN", { maximumFractionDigits: 2 })}` : "on a channel break"}
                  {program.spot != null && leg.trigger != null ? ` · ${Math.abs(leg.trigger - program.spot).toFixed(1)} points away` : ""}
                  {n.expiry ? ` · expiry ${calendarDay(n.expiry)}` : ""}
                </div>
              </div>
            ),
          )}
        </div>
      ) : (
        <Empty>Waiting for your program&apos;s first price.</Empty>
      )}
      <p className="text-2xs text-ink-muted">
        One strike in the money, at the nearest weekly expiry 2–8 days out, as the strategy picks them. Prices are
        not fetched while your program trades, to keep Angel&apos;s request budget for the program.
      </p>
    </div>
  );
}
