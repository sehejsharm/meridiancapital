"use client";

import { useCountUp, useDirection } from "@/lib/useCountUp";
import { useTicker } from "@/lib/useDeskFeeds";
import { istTime } from "@/lib/format";
import { istAgeMs } from "@/lib/link";
import { useLiveFeed } from "@/lib/LiveContext";

/** A published price older than this is not shown as the engine's live one. */
const LIVE_PRICE_MS = 90_000;

function Reading({
  label,
  value,
  digits = 2,
  tone,
}: {
  label: string;
  value: number | null;
  digits?: number;
  tone?: string;
}) {
  return (
    <div className="flex min-w-0 flex-col">
      <span className="truncate text-2xs uppercase tracking-[0.14em] text-ink-muted">{label}</span>
      <span className={`truncate text-sm font-semibold tabular-nums ${tone ?? "text-ink"}`}>
        {value === null ? "—" : value.toLocaleString("en-IN", {
          minimumFractionDigits: digits,
          maximumFractionDigits: digits,
        })}
      </span>
    </div>
  );
}

/**
 * The index, re-read every second.
 *
 * The spot is whatever a running engine last published, so when nothing is
 * running there is genuinely no price to show and the strip says so rather
 * than displaying a stale number as though it were live.
 */
export function NiftyTicker() {
  // The running engine's price arrives on the live socket every second; the
  // HTTP read is only the fallback when the socket does not carry one.
  const { snapshot } = useLiveFeed();
  const sig = snapshot?.signal;
  const pushedAge = istAgeMs(snapshot?.ts);
  const pushed = sig?.spot != null && pushedAge !== null && pushedAge < LIVE_PRICE_MS;
  const { data: polled, error } = useTicker(!pushed);
  const data = pushed
    ? {
        spot: sig!.spot, bar_close: sig!.bar_close, channel_high: sig!.channel_high,
        channel_low: sig!.channel_low, ts: snapshot!.ts, live: true, delayed: false,
        source_algo: "engine", label: null,
      }
    : polled;
  const spot = data?.spot ?? null;
  const eased = useCountUp(spot);
  const direction = useDirection(spot);

  const flash =
    direction === "up"
      ? "text-good"
      : direction === "down"
        ? "text-critical"
        : "text-ink";

  const room =
    data?.channel_high != null && spot != null ? data.channel_high - spot : null;
  const roomDown =
    data?.channel_low != null && spot != null ? spot - data.channel_low : null;

  return (
    <section
      aria-label="NIFTY spot"
      className="flex flex-wrap items-center gap-x-6 gap-y-3 rounded-lg border border-hairline bg-surface px-4 py-3"
    >
      <div className="flex items-baseline gap-3">
        <span className="text-2xs font-semibold uppercase tracking-[0.16em] text-brand">
          NIFTY 50
        </span>
        <span
          className={`text-2xl font-semibold tabular-nums transition-colors duration-300 sm:text-3xl ${flash}`}
        >
          {eased === null
            ? "—"
            : eased.toLocaleString("en-IN", {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2,
              })}
        </span>
        <span
          aria-hidden="true"
          className={`text-sm transition-opacity duration-300 ${
            direction ? "opacity-100" : "opacity-0"
          } ${direction === "down" ? "text-critical" : "text-good"}`}
        >
          {direction === "down" ? "▼" : "▲"}
        </span>
      </div>

      {/* A row of their own on a phone: as a shrinkable flex item the grid
          collapsed to nothing and its labels spilled across the status line. */}
      <div className="grid w-full grid-cols-2 gap-x-6 gap-y-2 sm:w-auto sm:min-w-[24rem] sm:flex-1 sm:grid-cols-4">
        <Reading label="Bar close" value={data?.bar_close ?? null} />
        <Reading label="Channel high" value={data?.channel_high ?? null} />
        <Reading label="Channel low" value={data?.channel_low ?? null} />
        <Reading
          label="Room to break"
          value={room != null && roomDown != null ? Math.min(room, roomDown) : null}
          digits={1}
        />
      </div>

      <div className="flex w-full min-w-0 items-center gap-2 text-2xs text-ink-muted sm:ml-auto sm:w-auto">
        <span
          className={`h-1.5 w-1.5 rounded-full ${
            data?.live ? "live-dot bg-good" : "bg-ink-muted"
          }`}
          aria-hidden="true"
        />
        {data?.live
          ? `${istTime(data.ts)} · live from the engine`
          : data?.delayed
            ? "public feed · about a minute behind"
            : error
              ? "price unavailable"
              : "no price yet"}
      </div>
    </section>
  );
}
