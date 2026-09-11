import type { Position } from "@/lib/types";
import { minutes, money, percent, points, pnlClass, signedMoney, signedPercent } from "@/lib/format";
import { Badge, Card, Empty, Field } from "@/components/ui";

/** The stop ladder is the whole risk story of an open trade, so it leads. */
export function PositionPanel({ position }: { position: Position | null }) {
  if (!position) {
    return (
      <Card title="Open position">
        <Empty>
          Flat. The engine takes at most one trade a day, on a Donchian-90 break inside the
          10:15–14:00 window.
        </Empty>
      </Card>
    );
  }

  const gain = position.gain_pct;
  const locked = position.stop_pct < 0;
  const breakeven = position.stop_pct === 0;
  const progressToTarget =
    position.index_move_pts != null
      ? Math.max(0, Math.min(1, position.index_move_pts / position.target_pts))
      : 0;

  return (
    <Card
      title="Open position"
      subtitle={`${position.tsym} · opened ${position.opened_ts.split("T")[1]?.slice(0, 5) ?? "—"} IST`}
      action={
        <Badge tone={position.side === "CE" ? "good" : "critical"}>
          {position.side === "CE" ? "Long call" : "Long put"}
        </Badge>
      }
    >
      <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div>
          <div className="text-2xs uppercase tracking-[0.14em] text-ink-muted">Unrealised</div>
          <div className={`mt-1 text-3xl font-semibold tabular-nums ${pnlClass(position.unrealised)}`}>
            {signedMoney(position.unrealised)}
          </div>
          <div className={`mt-1 text-sm font-medium tabular-nums ${pnlClass(gain)}`}>
            {signedPercent(gain)} on premium
          </div>

          <dl className="mt-4 divide-y divide-hairline">
            <Field label="Strike" value={`${position.strike} ${position.side} (ITM)`} />
            <Field label="Size" value={`${position.lots} lots · ${position.qty} qty`} />
            <Field label="Entry premium" value={money(position.entry_premium, true)} />
            <Field
              label="Live premium"
              value={position.live_premium != null ? money(position.live_premium, true) : "—"}
            />
            <Field label="Expiry" value={position.expiry} />
            <Field label="Time in trade" value={minutes(position.hold_min)} />
          </dl>
        </div>

        <div className="rounded-md border border-hairline bg-surface-raised p-3">
          <div className="text-2xs uppercase tracking-[0.14em] text-ink-muted">Stop ladder</div>
          <div
            className={`mt-1 text-lg font-semibold tabular-nums ${
              locked ? "text-good" : breakeven ? "text-warning" : "text-ink"
            }`}
          >
            {money(position.stop_price, true)}
          </div>
          <div className="text-2xs text-ink-secondary">
            {locked ? "Profit locked · " : breakeven ? "At break-even · " : "Initial stop · "}
            {position.stop_state}
          </div>

          <dl className="mt-3 divide-y divide-hairline">
            <Field label="Peak this trade" value={signedPercent(position.peak_pct)} />
            <Field label="Spot at entry" value={points(position.spot_entry, 0)} />
            <Field
              label="Index move"
              value={
                position.index_move_pts != null
                  ? `${position.index_move_pts >= 0 ? "+" : "−"}${Math.abs(position.index_move_pts).toFixed(0)} pts`
                  : "—"
              }
              tone={pnlClass(position.index_move_pts)}
            />
            <Field label="Target" value={`+${position.target_pts} pts`} />
          </dl>

          <div className="mt-3">
            <div className="flex items-baseline justify-between">
              <span className="text-2xs text-ink-secondary">Progress to target</span>
              <span className="text-2xs font-medium tabular-nums text-ink">
                {percent(progressToTarget, 0)}
              </span>
            </div>
            <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-surface">
              <div
                className="h-full rounded-full bg-series transition-[width] duration-500"
                style={{ width: `${progressToTarget * 100}%` }}
              />
            </div>
          </div>
        </div>
      </div>
    </Card>
  );
}
