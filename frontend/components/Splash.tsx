"use client";

import { useEffect, useState } from "react";

import { CARDINALS, DIAGONALS, RAY_COUNT, polar, spike } from "@/components/Logo";
import { SPLASH_EVENT, SPLASH_MS } from "@/lib/splash";

const RAYS = Array.from({ length: RAY_COUNT }, (_, i) => (360 / RAY_COUNT) * i).filter((a) => a % 45 !== 0);
const RINGS = [
  { r: 92, delay: 0 },
  { r: 70, delay: 80 },
  { r: 40, delay: 150 },
];
const RAY_LENGTH = 50;
// The fade-out, after the mark has finished moving.
const FADE_MS = 200;

/**
 * The animated mark shown on sign-in, on every page load, and when the logo
 * is tapped.
 *
 * Over one second the rings draw themselves, the rays sweep in clockwise, the
 * rose spins into place and settles, the centre lights with a pulse, and the
 * name tracks in beneath. The whole sequence is CSS, so on a page load it
 * starts with the first paint rather than waiting for the app's JavaScript,
 * and it leaves by itself even if that JavaScript is slow.
 */
export function Splash() {
  // Every play remounts the overlay under a new key, which restarts its CSS.
  // It starts on (1) so the server's HTML already carries it for a reload.
  const [run, setRun] = useState(1);
  const [shown, setShown] = useState(true);

  useEffect(() => {
    const play = () => {
      setRun((n) => n + 1);
      setShown(true);
    };
    window.addEventListener(SPLASH_EVENT, play);
    return () => window.removeEventListener(SPLASH_EVENT, play);
  }, []);

  // Out of the DOM once it has faded; the CSS has already hidden it by then.
  useEffect(() => {
    if (!shown) return;
    const t = setTimeout(() => setShown(false), SPLASH_MS + FADE_MS + 100);
    return () => clearTimeout(t);
  }, [shown, run]);

  if (!shown) return null;

  return (
    <div key={run} className="splash" aria-hidden="true" data-testid="splash">
      <div className="pointer-events-none absolute inset-0 plane-grid" />
      <div className="relative flex flex-col items-center">
        <div className="splash-glow" />
        <svg viewBox="0 0 200 200" className="splash-mark text-brand" fill="none">
          <g stroke="currentColor" strokeWidth="1.2" opacity="0.45">
            {RINGS.map(({ r, delay }) => {
              const len = 2 * Math.PI * r;
              return (
                <circle
                  key={r}
                  cx="100"
                  cy="100"
                  r={r}
                  className="splash-ring"
                  style={{ ["--len" as string]: len, animationDelay: `${delay}ms` }}
                  strokeDasharray={len}
                  transform="rotate(-90 100 100)"
                />
              );
            })}
          </g>

          <g stroke="currentColor" strokeWidth="0.9">
            {RAYS.map((angle, i) => {
              const [x1, y1] = polar(angle, 42);
              const [x2, y2] = polar(angle, 92);
              return (
                <line
                  key={angle}
                  x1={x1}
                  y1={y1}
                  x2={x2}
                  y2={y2}
                  className="splash-ray"
                  strokeDasharray={RAY_LENGTH}
                  style={{ ["--len" as string]: RAY_LENGTH, animationDelay: `${180 + i * 14}ms` }}
                />
              );
            })}
          </g>

          <g className="splash-rose" fill="currentColor">
            {DIAGONALS.map((angle) => (
              <path key={`d${angle}`} d={spike(angle, 68, 7)} opacity="0.75" />
            ))}
            {CARDINALS.map((angle) => (
              <path key={`c${angle}`} d={spike(angle, 94, 9)} />
            ))}
          </g>

          <circle cx="100" cy="100" r="11" className="splash-pulse" stroke="currentColor" strokeWidth="1.5" />
          <g className="splash-core">
            <circle cx="100" cy="100" r="11" fill="var(--plane)" stroke="currentColor" strokeWidth="2" />
            <circle cx="100" cy="100" r="3.5" fill="currentColor" />
          </g>
        </svg>

        <div className="splash-name mt-5 text-lg font-semibold text-ink">MERIDIAN</div>
        <div className="splash-sub mt-1 text-2xs tracking-[0.34em] text-brand">CAPITAL</div>
      </div>
    </div>
  );
}
