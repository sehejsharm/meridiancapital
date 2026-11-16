export interface PathPoint { t: number; v: number }

/** "10:31" or "2026-11-11T10:31:07" as minutes of the day (with seconds as a fraction). */
export function minuteOfDay(ts: string): number {
  const hms = ts.length > 8 ? ts.slice(11) : ts;
  const [h, m, s] = hms.split(":").map(Number);
  return h * 60 + m + (Number.isFinite(s) ? s / 60 : 0);
}

/** An SVG polyline for a series, scaled into a width x height box with the given ranges. */
export function polyline(points: PathPoint[], t0: number, t1: number, lo: number, hi: number, width: number, height: number): string {
  const dx = t1 - t0 || 1;
  const dy = hi - lo || 1;
  return points
    .map((p) => `${(((p.t - t0) / dx) * width).toFixed(1)},${(height - ((p.v - lo) / dy) * height).toFixed(1)}`)
    .join(" ");
}

/** The value range to draw, padded, covering every series and marker. */
export function range(values: number[]): [number, number] {
  const finite = values.filter((v) => Number.isFinite(v));
  if (!finite.length) return [0, 1];
  const lo = Math.min(...finite);
  const hi = Math.max(...finite);
  const pad = (hi - lo || Math.abs(hi) || 1) * 0.06;
  return [lo - pad, hi + pad];
}
