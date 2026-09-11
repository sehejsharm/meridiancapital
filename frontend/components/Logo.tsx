/**
 * Meridian Capital compass-rose mark, redrawn as vector so it stays crisp at
 * favicon size and inherits the brand gold from CSS rather than baking it in.
 */

const RAY_COUNT = 32;
const DIAGONALS = [45, 135, 225, 315];
const CARDINALS = [0, 90, 180, 270];

function polar(angleDeg: number, radius: number, cx = 100, cy = 100) {
  const rad = ((angleDeg - 90) * Math.PI) / 180;
  return [cx + radius * Math.cos(rad), cy + radius * Math.sin(rad)] as const;
}

function spike(angle: number, length: number, halfWidth: number) {
  const [tipX, tipY] = polar(angle, length);
  const [leftX, leftY] = polar(angle - 90, halfWidth);
  const [rightX, rightY] = polar(angle + 90, halfWidth);
  return `M ${tipX} ${tipY} L ${leftX} ${leftY} L ${rightX} ${rightY} Z`;
}

export function Logo({
  size = 40,
  className = "",
  withFrame = true,
}: {
  size?: number;
  className?: string;
  withFrame?: boolean;
}) {
  // Below ~48px the hairline rays and corner ticks collapse into mush, so the
  // small mark keeps only the parts that still read: the rose and one ring.
  const detailed = size >= 48;
  const rays = detailed
    ? Array.from({ length: RAY_COUNT }, (_, i) => (360 / RAY_COUNT) * i).filter((a) => a % 45 !== 0)
    : [];

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 200 200"
      className={className}
      role="img"
      aria-label="Meridian Capital"
      fill="none"
    >
      {withFrame && <rect width="200" height="200" rx="6" fill="var(--logo-plate)" />}

      <g
        stroke="currentColor"
        strokeWidth={detailed ? 1 : 4}
        opacity={detailed ? 0.35 : 0.5}
        fill="none"
      >
        <circle cx="100" cy="100" r="92" />
        {detailed && <circle cx="100" cy="100" r="70" />}
        {detailed && <circle cx="100" cy="100" r="40" />}
      </g>

      {/* Fine rays between the points give the mark its sunburst texture. */}
      <g stroke="currentColor" strokeWidth="0.9" opacity="0.55">
        {rays.map((angle) => {
          const [x1, y1] = polar(angle, 42);
          const [x2, y2] = polar(angle, 92);
          return <line key={angle} x1={x1} y1={y1} x2={x2} y2={y2} />;
        })}
      </g>

      <g fill="currentColor">
        {DIAGONALS.map((angle) => (
          <path key={`d${angle}`} d={spike(angle, 68, 7)} opacity="0.75" />
        ))}
        {CARDINALS.map((angle) => (
          <path key={`c${angle}`} d={spike(angle, 94, 9)} />
        ))}
      </g>

      <circle
        cx="100"
        cy="100"
        r={detailed ? 11 : 15}
        fill="var(--logo-plate)"
        stroke="currentColor"
        strokeWidth={detailed ? 2 : 5}
      />
      <circle cx="100" cy="100" r={detailed ? 3.5 : 5} fill="currentColor" />

      {withFrame && detailed && (
        <g stroke="currentColor" strokeWidth="1.2" opacity="0.5">
          {[
            [16, 16],
            [184, 16],
            [16, 184],
            [184, 184],
          ].map(([x, y]) => (
            <g key={`${x}-${y}`}>
              <line x1={x - 5} y1={y} x2={x + 5} y2={y} />
              <line x1={x} y1={y - 5} x2={x} y2={y + 5} />
            </g>
          ))}
        </g>
      )}
    </svg>
  );
}

export function Wordmark({ size = 34 }: { size?: number }) {
  return (
    <div className="flex items-center gap-3">
      <Logo size={size} className="text-brand shrink-0" />
      <div className="leading-none">
        <div className="text-[15px] font-semibold tracking-[0.18em] text-ink">MERIDIAN</div>
        <div className="text-2xs tracking-[0.32em] text-brand">CAPITAL</div>
      </div>
    </div>
  );
}
