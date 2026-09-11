import type { Snapshot } from "@/lib/types";
import { duration, points } from "@/lib/format";
import { Badge, Card, Field } from "@/components/ui";

const STATE_COPY = {
  break_up: { label: "Break up — long call live", tone: "good" as const },
  break_down: { label: "Break down — long put live", tone: "critical" as const },
  inside: { label: "Inside channel", tone: "neutral" as const },
  unknown: { label: "No data", tone: "neutral" as const },
};

/**
 * The signal and the data-integrity guards that can veto it. A breakout the
 * engine refuses to trade because the feed is stale must be legible here, or
 * the dashboard and the engine appear to disagree.
 */
export function SignalPanel({ snapshot }: { snapshot: Snapshot }) {
  const { signal, market } = snapshot;
  const state = STATE_COPY[signal.state];

  const feedStale =
    signal.bar_age_sec != null && signal.bar_age_sec > 180;
  const feedDiverged =
    signal.divergence_pts != null && signal.divergence_pts > signal.divergence_limit;
  const thinHistory = signal.bars_loaded > 0 && signal.bars_loaded < signal.lookback + 2;
  const feedOk = !feedStale && !feedDiverged && !thinHistory && signal.bars_loaded > 0;

  return (
    <Card
      title="Signal"
      subtitle={`Donchian-${signal.lookback} breakout · entries ${market.entry_window.start}–${market.entry_window.cutoff}, flat by ${market.entry_window.force_close}`}
      action={<Badge tone={state.tone}>{state.label}</Badge>}
    >
      <div className="mb-4">
        <div className="text-2xs uppercase tracking-[0.14em] text-ink-muted">NIFTY spot</div>
        <div className="mt-0.5 text-3xl font-semibold tabular-nums text-ink">
          {signal.spot != null ? points(signal.spot) : "—"}
        </div>
      </div>

      <ChannelBar
        low={signal.channel_low}
        high={signal.channel_high}
        spot={signal.spot}
      />

      <dl className="mt-4 divide-y divide-hairline">
        <Field
          label="Channel"
          value={
            signal.channel_low != null && signal.channel_high != null
              ? `${points(signal.channel_low, 0)} … ${points(signal.channel_high, 0)}`
              : "—"
          }
        />
        <Field
          label="Room to break up"
          value={signal.room_up != null ? `${points(signal.room_up, 0)} pts` : "—"}
        />
        <Field
          label="Room to break down"
          value={signal.room_down != null ? `${points(signal.room_down, 0)} pts` : "—"}
        />
        {signal.next_strike != null && (
          <Field label="Strike on trigger" value={`${signal.next_strike} (ITM50)`} />
        )}
      </dl>

      <div className="mt-4 rounded-md border border-hairline bg-surface-raised p-3">
        <div className="flex items-center justify-between">
          <span className="text-2xs uppercase tracking-[0.14em] text-ink-muted">
            Feed integrity
          </span>
          <Badge tone={feedOk ? "good" : "warning"}>
            {feedOk ? "Clean" : "Entry blocked"}
          </Badge>
        </div>
        <dl className="mt-2 divide-y divide-hairline">
          <Field
            label="Candle vs LTP"
            value={
              signal.divergence_pts != null
                ? `${signal.divergence_pts.toFixed(1)} / ${signal.divergence_limit.toFixed(0)} pts`
                : "—"
            }
            tone={feedDiverged ? "text-warning" : "text-ink"}
          />
          <Field
            label="Last candle age"
            value={duration(signal.bar_age_sec)}
            tone={feedStale ? "text-warning" : "text-ink"}
          />
          <Field
            label="Bars loaded"
            value={`${signal.bars_loaded} / ${signal.lookback + 2} needed`}
            tone={thinHistory ? "text-warning" : "text-ink"}
          />
        </dl>
      </div>
    </Card>
  );
}

function ChannelBar({
  low,
  high,
  spot,
}: {
  low: number | null;
  high: number | null;
  spot: number | null;
}) {
  if (low == null || high == null || spot == null || high <= low) return null;

  const pad = (high - low) * 0.25;
  const min = low - pad;
  const max = high + pad;
  const pct = (v: number) => ((v - min) / (max - min)) * 100;
  const clamped = Math.min(100, Math.max(0, pct(spot)));

  return (
    <div className="pt-1">
      <div className="relative h-9">
        <div className="absolute inset-x-0 top-4 h-1.5 rounded-full bg-surface-raised" />
        <div
          className="absolute top-4 h-1.5 rounded-full bg-brand-dim"
          style={{ left: `${pct(low)}%`, width: `${pct(high) - pct(low)}%` }}
        />
        {[low, high].map((edge, i) => (
          <div
            key={edge}
            className="absolute top-2.5 h-4 w-px bg-baseline"
            style={{ left: `${pct(edge)}%` }}
            aria-label={i === 0 ? "channel low" : "channel high"}
          />
        ))}
        <div
          className="absolute top-2 h-5 w-1 -translate-x-1/2 rounded-full bg-series ring-2 ring-[color:var(--surface-1)]"
          style={{ left: `${clamped}%` }}
        />
      </div>
      <div className="flex justify-between text-2xs tabular-nums text-ink-muted">
        <span>{points(low, 0)}</span>
        <span>{points(high, 0)}</span>
      </div>
    </div>
  );
}
