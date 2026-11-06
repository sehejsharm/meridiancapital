/** The option chain's OI bars and skew chart (lib/chain.ts). */
import { test } from "node:test";
import assert from "node:assert/strict";

import { oiPct, signedCompact, skewChart } from "../lib/chain.ts";
import type { ChainRow, ChainSide } from "../lib/types.ts";

const side = (iv: number | null): ChainSide =>
  ({ tsym: "", token: "", ltp: 1, change: null, change_pct: null, volume: null, oi: null, bid: null, ask: null,
     high: null, low: null, iv, delta: null, gamma: null, theta: null, vega: null });

test("OI bars are a share of the heaviest strike", () => {
  assert.equal(oiPct(450, 900), 50);
  assert.equal(oiPct(null, 900), 0);
  assert.equal(oiPct(10, 0), 0);
  assert.equal(oiPct(1000, 900), 100);
});

test("OI change reads signed, in Indian units", () => {
  assert.equal(signedCompact(12_300), "+12.3K");
  assert.equal(signedCompact(-450_000), "−4.50L");
  assert.equal(signedCompact(0), "0");
  assert.equal(signedCompact(null), "—");
});

test("the skew chart spans the strikes and the vols shown", () => {
  const rows: ChainRow[] = [
    { strike: 24400, ce: side(14), pe: side(16) },
    { strike: 24500, ce: side(13), pe: side(15) },
    { strike: 24600, ce: side(12), pe: null },
  ];
  const c = skewChart(rows, 100, 50, 0)!;
  assert.equal(c.lo, 12);
  assert.equal(c.hi, 16);
  assert.equal(c.x(24400), 0);
  assert.equal(c.x(24600), 100);
  assert.equal(c.ce, "M0.0,25.0 L50.0,37.5 L100.0,50.0");
  assert.equal(c.pe, "M0.0,0.0 L50.0,12.5", "a missing put is skipped, not drawn at zero");
  assert.equal(skewChart(rows.slice(0, 1), 100, 50), null);
});
