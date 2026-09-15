"use client";

import { useEffect, useRef, useState } from "react";

/**
 * Eases a number towards its target so figures glide rather than jump.
 *
 * Purely presentational: the value shown always converges on the real one, and
 * it snaps immediately for a change large enough that animating it would be
 * misleading about how fast the market actually moved.
 */
export function useCountUp(target: number | null, durationMs = 450): number | null {
  const [display, setDisplay] = useState<number | null>(target);
  const frame = useRef<number | null>(null);
  const from = useRef<number | null>(target);
  const started = useRef(0);

  useEffect(() => {
    if (target === null) {
      setDisplay(null);
      return;
    }
    const start = from.current;
    if (start === null || Math.abs(target - start) > Math.max(1, Math.abs(start) * 0.05)) {
      from.current = target;
      setDisplay(target);
      return;
    }
    started.current = performance.now();

    const step = (now: number) => {
      const t = Math.min(1, (now - started.current) / durationMs);
      const eased = 1 - (1 - t) ** 3;
      const value = start + (target - start) * eased;
      setDisplay(value);
      if (t < 1) {
        frame.current = requestAnimationFrame(step);
      } else {
        from.current = target;
      }
    };
    frame.current = requestAnimationFrame(step);
    return () => {
      if (frame.current) cancelAnimationFrame(frame.current);
    };
  }, [target, durationMs]);

  return display;
}

/** "up" | "down" | null for the most recent change — drives flash colouring. */
export function useDirection(value: number | null, resetMs = 900): "up" | "down" | null {
  const [dir, setDir] = useState<"up" | "down" | null>(null);
  const prev = useRef<number | null>(value);

  useEffect(() => {
    if (value === null || prev.current === null || value === prev.current) {
      prev.current = value;
      return;
    }
    setDir(value > prev.current ? "up" : "down");
    prev.current = value;
    const t = setTimeout(() => setDir(null), resetMs);
    return () => clearTimeout(t);
  }, [value, resetMs]);

  return dir;
}
