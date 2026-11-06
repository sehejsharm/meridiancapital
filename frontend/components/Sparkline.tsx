/**
 * A trend in a line, small enough for a tile. Pure SVG: no chart library for
 * thirty points, and it paints with the page.
 */
export function Sparkline({
  values,
  height = 28,
  className = "text-brand",
  fill = true,
  label,
}: {
  values: (number | null | undefined)[];
  height?: number;
  className?: string;
  fill?: boolean;
  label?: string;
}) {
  const pts = values.map((v) => (v == null || !Number.isFinite(v) ? null : v));
  const nums = pts.filter((v): v is number => v !== null);
  if (nums.length < 2) return null;
  const w = 100;
  const lo = Math.min(...nums);
  const hi = Math.max(...nums);
  const span = hi - lo || 1;
  const step = w / (pts.length - 1);
  const xy = pts
    .map((v, i) => (v === null ? null : [i * step, height - 2 - ((v - lo) / span) * (height - 4)] as const))
    .filter((p): p is readonly [number, number] => p !== null);
  const line = xy.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(2)},${y.toFixed(2)}`).join(" ");
  const area = `${line} L${xy[xy.length - 1][0].toFixed(2)},${height} L${xy[0][0].toFixed(2)},${height} Z`;
  return (
    <svg
      viewBox={`0 0 ${w} ${height}`}
      preserveAspectRatio="none"
      className={`block h-[var(--h)] w-full ${className}`}
      style={{ ["--h" as string]: `${height}px` }}
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    >
      {fill && <path d={area} fill="currentColor" opacity={0.12} />}
      <path d={line} fill="none" stroke="currentColor" strokeWidth={1.4} vectorEffect="non-scaling-stroke" />
      <circle cx={xy[xy.length - 1][0]} cy={xy[xy.length - 1][1]} r={1.6} fill="currentColor" />
    </svg>
  );
}
