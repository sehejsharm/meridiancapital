import assert from "node:assert/strict";
import { test } from "node:test";

import { band, verdict, verdictText } from "../lib/expectation.ts";

const exp = { mean: 300, sd: 3000 };
const flat = (n: number, net: number, lots = 1) => Array.from({ length: n }, (_, i) => ({ day: `2026-11-${String(i + 1).padStart(2, "0")}`, net, lots }));

test("the band grows with the square root of the number of trades", () => {
  const p = band(exp, flat(4, 500));
  assert.deepEqual(p[0], { n: 1, day: "2026-11-01", actual: 500, expected: 300, low: -5700, high: 6300 });
  assert.equal(p[3].actual, 2000);
  assert.equal(p[3].expected, 1200);
  assert.equal(p[3].low, 1200 - 2 * 3000 * 2);     // sqrt(4) = 2
  assert.equal(p[3].high, 1200 + 12000);
});

test("a trade of two lots is expected to make twice as much and swing twice as far", () => {
  const p = band(exp, flat(1, 0, 2));
  assert.equal(p[0].expected, 600);
  assert.equal(p[0].low, 600 - 12000);
});

test("no verdict before ten trades, however bad", () => {
  assert.equal(verdict(band(exp, [])), "none");
  assert.equal(verdict(band(exp, flat(9, -9000))), "early");
  assert.match(verdictText("early", 9), /9 trades so far/);
});

test("inside, below and above the band", () => {
  assert.equal(verdict(band(exp, flat(16, -400))), "inside");    // -6,400 against a floor of 4,800 - 24,000
  assert.equal(verdict(band(exp, flat(16, -1300))), "below");    // -20,800 < -19,200
  assert.equal(verdict(band(exp, flat(16, 2000))), "above");     // 32,000 > 28,800
});
