import type { ReactNode } from "react";

import { clamp01, pnlClass } from "@/lib/format";

export function Card({
  title,
  subtitle,
  action,
  children,
  className = "",
}: {
  title?: string;
  subtitle?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`rounded-lg border border-hairline bg-surface ${className}`}
    >
      {(title || action) && (
        <header className="flex items-start justify-between gap-4 border-b border-hairline px-4 py-3">
          <div className="min-w-0">
            {title && (
              <h2 className="text-2xs font-semibold uppercase tracking-[0.16em] text-brand">
                {title}
              </h2>
            )}
            {subtitle && <p className="mt-1 text-xs text-ink-muted">{subtitle}</p>}
          </div>
          {action}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function StatTile({
  label,
  value,
  delta,
  hint,
  tone = "neutral",
}: {
  label: string;
  value: string;
  delta?: { text: string; value: number };
  hint?: string;
  tone?: "neutral" | "brand";
}) {
  return (
    <div className="rounded-lg border border-hairline bg-surface px-4 py-3">
      <div className="text-2xs uppercase tracking-[0.14em] text-ink-muted">{label}</div>
      <div
        className={`mt-1.5 text-2xl font-semibold tabular-nums ${
          tone === "brand" ? "text-brand" : "text-ink"
        }`}
      >
        {value}
      </div>
      {delta && (
        <div className={`mt-1 text-xs font-medium tabular-nums ${pnlClass(delta.value)}`}>
          {delta.text}
        </div>
      )}
      {hint && <div className="mt-1 text-2xs text-ink-muted">{hint}</div>}
    </div>
  );
}

type MeterTone = "neutral" | "good" | "warning" | "critical";

const METER_FILL: Record<MeterTone, string> = {
  neutral: "bg-series",
  good: "bg-good",
  warning: "bg-warning",
  critical: "bg-critical",
};

/**
 * A risk meter reads "how close to the limit", so it is scaled by used/limit and
 * escalates tone as it fills. The numbers are always shown beside it; the bar is
 * never the only carrier of the value.
 */
export function Meter({
  label,
  used,
  limit,
  display,
  invert = false,
}: {
  label: string;
  used: number;
  limit: number;
  display: string;
  invert?: boolean;
}) {
  const fraction = limit === 0 ? 0 : clamp01(Math.abs(used) / Math.abs(limit));
  const tone: MeterTone = invert
    ? fraction >= 1
      ? "good"
      : "neutral"
    : fraction >= 0.85
      ? "critical"
      : fraction >= 0.6
        ? "warning"
        : "good";

  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-xs text-ink-secondary">{label}</span>
        <span className="text-xs font-medium tabular-nums text-ink">{display}</span>
      </div>
      <div
        className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-surface-raised"
        role="meter"
        aria-label={label}
        aria-valuenow={Math.round(fraction * 100)}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div
          className={`h-full rounded-full transition-[width] duration-500 ${METER_FILL[tone]}`}
          style={{ width: `${fraction * 100}%` }}
        />
      </div>
    </div>
  );
}

const BADGE_TONE = {
  neutral: "border-hairline text-ink-secondary",
  brand: "border-brand/40 bg-brand-dim text-brand",
  good: "border-good/40 text-good",
  warning: "border-warning/50 text-warning",
  serious: "border-serious/50 text-serious",
  critical: "border-critical/50 text-critical",
} as const;

export function Badge({
  children,
  tone = "neutral",
  dot = false,
}: {
  children: ReactNode;
  tone?: keyof typeof BADGE_TONE;
  dot?: boolean;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-0.5 text-2xs font-medium uppercase tracking-[0.1em] ${BADGE_TONE[tone]}`}
    >
      {dot && <span className="live-dot h-1.5 w-1.5 rounded-full bg-current" />}
      {children}
    </span>
  );
}

export function Field({
  label,
  value,
  tone,
  mono = true,
}: {
  label: string;
  value: ReactNode;
  tone?: string;
  mono?: boolean;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1.5">
      <dt className="text-xs text-ink-secondary">{label}</dt>
      <dd className={`text-xs font-medium ${mono ? "tabular-nums" : ""} ${tone ?? "text-ink"}`}>
        {value}
      </dd>
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-[7rem] items-center justify-center rounded-md border border-dashed border-hairline px-4 py-6 text-center text-xs text-ink-muted">
      {children}
    </div>
  );
}

export function Button({
  children,
  onClick,
  variant = "default",
  disabled,
  type = "button",
  full = false,
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "default" | "primary" | "danger" | "ghost";
  disabled?: boolean;
  type?: "button" | "submit";
  full?: boolean;
}) {
  const styles = {
    default: "border-hairline bg-surface-raised text-ink hover:border-brand/50",
    primary: "border-brand bg-brand text-[color:var(--plane)] hover:bg-brand-bright",
    danger: "border-critical/60 bg-critical/10 text-critical hover:bg-critical/20",
    ghost: "border-transparent text-ink-secondary hover:text-ink",
  }[variant];

  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex items-center justify-center gap-2 rounded-md border px-3 py-2 text-xs font-semibold uppercase tracking-[0.1em] transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${styles} ${full ? "w-full" : ""}`}
    >
      {children}
    </button>
  );
}
