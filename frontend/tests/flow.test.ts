import assert from "node:assert/strict";
import { test } from "node:test";

import { flowBars, liquidityRows, liquiditySummary, READING, rupeesCompact, share, spreadPct, spreadTone } from "../lib/flow.ts";
import type { ChainRow, ChainSide } from "../lib/types.ts";

const side = (ltp: number, bid: number | null, ask: number | null, volume: number, oi: number): ChainSide => ({
  tsym: "X", token: "1", ltp, change: null, change_pct: null, volume, oi, bid, ask, high: null, low: null,
  iv: null, delta: null, gamma: null, theta: null, vega: null,
});

test("the spread is a share of the mid price, and graded", () => {
  assert.equal(spreadPct(side(100, 99.5, 100.5, 0, 0)), 1);
  assert.equal(spreadPct(side(100, null, 100.5, 0, 0)), null);
  assert.equal(spreadPct(side(100, 101, 100, 0, 0)), null);       // a crossed quote is not a spread
  assert.equal(spreadTone(0.4), "tight");
  assert.equal(spreadTone(1.5), "fair");
  assert.equal(spreadTone(4), "wide");
  assert.equal(spreadTone(null), "none");
});

test("liquidity across the strikes: totals, the busiest contract, tightest and widest spreads", () => {
  const rows: ChainRow[] = [
    { strike: 25250, ce: side(120, 119.9, 120.1, 400000, 900000), pe: side(40, 39, 41, 150000, 1200000) },
    { strike: 25300, ce: side(90, 89.5, 90.5, 900000, 1500000), pe: side(60, 59.9, 60.1, 700000, 1000000) },
  ];
  const s = liquiditySummary(liquidityRows(rows));
  assert.equal(s.ceVol, 1300000);
  assert.equal(s.peVol, 850000);
  assert.equal(s.pcrVolume?.toFixed(3), "0.654");
  assert.deepEqual(s.busiest, { strike: 25300, side: "CE", volume: 900000 });
  assert.equal(s.tightest?.strike, 25250);
  assert.equal(s.tightest?.side, "CE");
  assert.equal(s.widest?.strike, 25250);
  assert.equal(s.widest?.side, "PE");                               // 2 on 40 = 5%
});

test("mirrored flow bars share one rupee scale", () => {
  const g = flowBars([{ t: "09:31", ce: 200, pe: 100 }, { t: "09:32", ce: 50, pe: 400 }], 100, 40);
  assert.equal(g.max, 400);
  assert.equal(g.mid, 20);
  assert.equal(g.bars[1].peH, 18);                                  // the heaviest bar fills its half (less 2)
  assert.equal(g.bars[0].ceH, 9);
  assert.equal(g.bars[1].x, 50);
});

test("words and units", () => {
  assert.equal(rupeesCompact(12345678), "₹1.23Cr");
  assert.equal(rupeesCompact(90287900000), "₹9,029Cr");                // a phone-width figure, not ₹9028.79Cr
  assert.equal(rupeesCompact(450000), "₹4.50L");
  assert.equal(rupeesCompact(-12300), "−₹12.3K");
  assert.equal(READING.short_buildup.short, "New writers");
  assert.equal(share(50, 200), 25);
  assert.equal(share(500, 200), 100);
});
