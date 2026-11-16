"use client";

import Link from "next/link";

import { ExitPlanCard } from "@/components/ExitPlan";
import { NoTradeNotice } from "@/components/NoTradeNotice";
import { Badge, Card, Empty } from "@/components/ui";
import { minutes, money, pnlClass, signedMoney, signedPercent } from "@/lib/format";
import { useLiveFeed } from "@/lib/LiveContext";
import { recordedAt, useDeskSnapshot } from "@/lib/recorded";

/** The day on one phone screen: the P&L, the open trade with its stop, and the way to the controls. */
export default function TodayPage() {
  const { status } = useLiveFeed();
  const { snapshot, live } = useDeskSnapshot();
  const account = snapshot?.account;
  const pos = live ? (snapshot?.position ?? null) : null;
  const liveCount = status?.fleet?.live_running ?? 0;
  return (
    <div className="mx-auto max-w-xl space-y-4">
      <NoTradeNotice always />
      <Card
        title="Today"
        subtitle={live ? `${snapshot?.engine?.banner ?? "Running"} · updated ${recordedAt(snapshot?.ts)}` : snapshot ? `Nothing running · last figures ${recordedAt(snapshot.ts)}` : "Nothing running"}
        action={<Badge tone={liveCount ? "critical" : "neutral"} glow={liveCount > 0}>{liveCount ? "Real money" : live ? "Paper" : "Idle"}</Badge>}
      >
        {!account ? (
          <Empty>No figures yet today.</Empty>
        ) : (
          <>
            <div className="text-2xs uppercase tracking-[0.14em] text-ink-muted">Day&apos;s P&amp;L after charges</div>
            <div className={`mt-1 text-4xl font-semibold tabular-nums ${pnlClass(account.day_pl)}`}>{signedMoney(account.day_pl)}</div>
            <div className="mt-1 text-xs tabular-nums text-ink-secondary">Balance {money(account.equity, true)}</div>
          </>
        )}
      </Card>

      {pos ? (
        <>
          <Card title="Open trade" subtitle={`${pos.tsym} · ${pos.lots} lot · held ${minutes(pos.hold_min)}`}>
            <div className={`text-3xl font-semibold tabular-nums ${pnlClass(pos.unrealised)}`}>{signedMoney(pos.unrealised)}</div>
            <div className={`mt-1 text-sm tabular-nums ${pnlClass(pos.gain_pct)}`}>{signedPercent(pos.gain_pct != null ? pos.gain_pct / 100 : null)} on premium</div>
            <dl className="mt-3 grid grid-cols-3 gap-2 text-xs">
              <div><dt className="text-ink-muted">Bought</dt><dd className="tabular-nums text-ink">{money(pos.entry_premium, true)}</dd></div>
              <div><dt className="text-ink-muted">Now</dt><dd className="tabular-nums text-ink">{pos.live_premium != null ? money(pos.live_premium, true) : "—"}</dd></div>
              <div><dt className="text-ink-muted">Stop</dt><dd className="tabular-nums text-ink">{pos.stop_price ? money(pos.stop_price, true) : "—"}</dd></div>
            </dl>
          </Card>
          <ExitPlanCard position={pos} />
        </>
      ) : (
        <Card title="Open trade"><Empty>Flat. Nothing is held right now.</Empty></Card>
      )}

      <div className="grid grid-cols-3 gap-2">
        <Link href="/controls" className="rounded-md border border-critical/60 bg-critical/10 px-2 py-3 text-center text-xs font-semibold uppercase tracking-[0.1em] text-critical touch:min-h-[44px]">Stop / flatten</Link>
        <Link href="/trades" className="rounded-md border border-hairline bg-surface-raised px-2 py-3 text-center text-xs font-semibold uppercase tracking-[0.1em] text-ink touch:min-h-[44px]">Trades</Link>
        <Link href="/" className="rounded-md border border-hairline bg-surface-raised px-2 py-3 text-center text-xs font-semibold uppercase tracking-[0.1em] text-ink touch:min-h-[44px]">Full deck</Link>
      </div>
    </div>
  );
}
