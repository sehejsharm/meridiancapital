import { Badge, Card } from "@/components/ui";
import { describePlan } from "@/lib/exitPlan";
import { money, signedPercent } from "@/lib/format";
import type { Position } from "@/lib/types";

/**
 * What the exit will do next on the open trade: the stop now, the next step up
 * and how far away it is, and when the late take-profit switches on. Worked out
 * by the API from the running program's own settings; the program decides.
 */
export function ExitPlanCard({ position }: { position: Position | null }) {
  const plan = position?.exit_plan;
  if (!position || !plan) return null;
  const v = describePlan(plan, position.live_premium, position.stop_price || null);
  return (
    <Card
      title="What the exit will do next"
      subtitle="From the running program's own settings. The program decides; this only reads them."
      action={<Badge tone={plan.stop_gain > 0 ? "good" : plan.stop_gain === 0 ? "neutral" : "warning"}>{v.stage}</Badge>}
    >
      <dl className="space-y-3 text-xs">
        <div>
          <dt className="text-2xs uppercase tracking-[0.14em] text-ink-muted">Stop now</dt>
          <dd className="mt-0.5 text-lg font-semibold tabular-nums text-ink">{v.stop}</dd>
          <dd className="text-ink-secondary">{v.stopNote}</dd>
        </div>
        <div>
          <dt className="text-2xs uppercase tracking-[0.14em] text-ink-muted">Next step</dt>
          <dd className="mt-0.5 text-ink">{v.next ?? "Every step has been reached: the stop follows the best price from here."}</dd>
          {v.nextDistance && <dd className="tabular-nums text-ink-secondary">{v.nextDistance}</dd>}
        </div>
        {v.late && (
          <div>
            <dt className="text-2xs uppercase tracking-[0.14em] text-ink-muted">Late take-profit</dt>
            <dd className="mt-0.5 text-ink">{v.late}</dd>
          </div>
        )}
        <div className="border-t border-hairline pt-2 text-2xs text-ink-muted">
          Best so far {signedPercent(plan.peak_gain)} · entry {money(position.entry_premium, true)} · {v.close}
        </div>
      </dl>
    </Card>
  );
}
