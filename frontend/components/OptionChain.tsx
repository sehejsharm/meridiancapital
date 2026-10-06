"use client";

import { useEffect, useMemo, useState } from "react";

import { Badge, Card, Empty } from "@/components/ui";
import { apiGet } from "@/lib/client-api";
import { calendarDay } from "@/lib/holidays";
import type { ChainPayload, ChainSide, ProgramContract } from "@/lib/types";

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

          <div className="-mx-4 overflow-x-auto px-4">
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
                      <td className={`hidden py-1.5 pr-2 text-right md:table-cell ${cell(heldCe, itmCe)}`}>{compact(row.ce?.oi)}</td>
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
                      <td className={`hidden py-1.5 pl-2 text-left md:table-cell ${cell(heldPe, !itmCe)}`}>{compact(row.pe?.oi)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="text-2xs text-ink-muted">
            Shaded cells are in the money; the gold cell is the contract held. {chain.model}.
          </p>
        </div>
      )}
    </Card>
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

const rupees = (v: number | null | undefined, digits = 0) =>
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
  const h = program.held;
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
          <Stat label="Target" value={h.target_level != null ? `NIFTY ${h.target_level.toLocaleString("en-IN")}` : "—"} hint={program.spot != null && h.target_level != null ? `${Math.abs(h.target_level - program.spot).toFixed(0)} points away` : undefined} />
          <Stat label="Implied vol" value={g ? `${g.iv.toFixed(1)}%` : "—"} />
          <Stat label="Delta" value={g ? g.delta.toFixed(2) : "—"} hint={g?.delta_position != null ? `₹${Math.abs(g.delta_position).toFixed(0)} per NIFTY point` : undefined} />
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
