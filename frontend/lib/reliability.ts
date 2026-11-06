import type { ReliabilityReport } from "@/lib/types";

/** "up 40% on last week", "down 12%…", "flat", or why there is no comparison. */
export function trendText(trend: ReliabilityReport["api"]["trend"]): string {
  if (!trend) return "not enough history for a week-on-week comparison";
  if (trend.change_pct === null) return `${trend.this_week} this week, none last week`;
  if (trend.change_pct === 0) return `${trend.this_week} this week, the same as last week`;
  const dir = trend.change_pct > 0 ? "up" : "down";
  return `${trend.this_week} this week, ${dir} ${Math.abs(trend.change_pct).toFixed(0)}% on last week's ${trend.last_week}`;
}

/** Good at or above 98% of market hours, a warning below, critical below 90%. */
export function uptimeTone(pct: number | null | undefined): "good" | "warning" | "critical" | "neutral" {
  if (pct == null) return "neutral";
  if (pct >= 98) return "good";
  if (pct >= 90) return "warning";
  return "critical";
}

/** The kinds of Angel API trouble, in the order the chart stacks them. */
export const API_KINDS = [
  { key: "timeouts", label: "Timeouts", className: "bg-warning" },
  { key: "rate_limits", label: "Rate limits", className: "bg-serious" },
  { key: "upstream", label: "Upstream errors", className: "bg-critical" },
  { key: "other_errors", label: "Other errors", className: "bg-series" },
] as const;
