"use client";

import { useEffect, useMemo, useState } from "react";

import { Badge, Card, Empty } from "@/components/ui";
import { apiGet } from "@/lib/client-api";
import type { ChainPayload, ChainSide } from "@/lib/types";

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
