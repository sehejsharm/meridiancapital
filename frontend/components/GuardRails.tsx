import type { Guards } from "@/lib/types";
import { money, percent } from "@/lib/format";
import { Badge, Card, Meter } from "@/components/ui";

/**
 * Every kill switch the engine enforces, shown as headroom against its limit.
 * A halt is announced with a labelled badge, never by colour alone.
 */
export function GuardRails({
  guards,
  drawdownPct,
}: {
  guards: Guards;
  drawdownPct: number;
}) {
  const halts: { label: string; tone: "critical" | "warning" | "good" }[] = [];
  if (guards.week_halted) halts.push({ label: "Weekly kill active", tone: "critical" });
  if (guards.halted) halts.push({ label: "Entries halted", tone: "critical" });
  if (guards.locked_profit) halts.push({ label: "Profit banked", tone: "good" });
  if (!guards.capital_ok) halts.push({ label: "Below capital floor", tone: "warning" });

  return (
    <Card
      title="Risk guards"
      subtitle="Hard limits enforced by the engine, not by the dashboard"
      action={
        halts.length ? (
          <div className="flex flex-wrap justify-end gap-1.5">
            {halts.map((h) => (
              <Badge key={h.label} tone={h.tone}>
                {h.label}
              </Badge>
            ))}
          </div>
        ) : (
          <Badge tone="good">All clear</Badge>
        )
      }
    >
      <div className="space-y-3.5">
        <Meter
          label="Daily loss limit"
          used={guards.daily_loss_used}
          limit={guards.daily_loss_limit}
          display={`${money(guards.daily_loss_used)} / ${money(-guards.daily_loss_limit)}`}
        />
        <Meter
          label="Weekly loss limit"
          used={guards.weekly_loss_used}
          limit={guards.weekly_loss_limit}
          display={`${money(guards.weekly_loss_used)} / ${money(-guards.weekly_loss_limit)}`}
        />
        <Meter
          label="Drawdown from peak"
          used={drawdownPct}
          limit={guards.drawdown_stop}
          display={`${percent(drawdownPct)} / −${percent(guards.drawdown_stop, 0)}`}
        />
        <Meter
          label="Profit lock"
          used={guards.profit_lock_progress}
          limit={guards.profit_lock_target}
          display={`${money(guards.profit_lock_progress)} / ${money(guards.profit_lock_target)}`}
          invert
        />
        <Meter
          label="Consecutive losses"
          used={guards.consec_losses}
          limit={guards.consec_loss_halt}
          display={`${guards.consec_losses} / ${guards.consec_loss_halt}`}
        />
        <Meter
          label="Trades today"
          used={guards.trades_today}
          limit={guards.max_trades_day}
          display={`${guards.trades_today} / ${guards.max_trades_day}`}
        />
      </div>

      <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-2 border-t border-hairline pt-3 text-2xs">
        <Sizing label="Deploy per trade" value={percent(guards.deploy_fraction, 0)} />
        <Sizing label="Position cap" value={percent(guards.per_trade_equity_cap, 0)} />
        <Sizing label="Per-trade risk cap" value={money(guards.per_trade_risk_rs)} />
        <Sizing label="Max lots" value={String(guards.max_lots)} />
        <Sizing label="Capital floor" value={money(guards.min_capital)} />
      </dl>
    </Card>
  );
}

function Sizing({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <dt className="text-ink-muted">{label}</dt>
      <dd className="font-medium tabular-nums text-ink-secondary">{value}</dd>
    </div>
  );
}
