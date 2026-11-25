/**
 * The position panel's line on the trail (lib/exitPlan.ts). 25 Nov 2026: with v18 running, the panel said
 * the stop "follows 20% of the entry price behind the best price" from the start; v18's trail starts 27.5%
 * behind and narrows to 20% by 180 minutes held.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { trailSentence } from "../lib/exitPlan.ts";
import type { ExitPlan } from "../lib/types.ts";

const plan = (o: Partial<ExitPlan>): ExitPlan => ({
  stage: "trailing", stop_gain: 0.25, stop_price: 250, locked_in: 0.25, peak_gain: 0.5, steps: [], next: null,
  late_tp: null, force_close: "15:10", ...o,
});

test("v18: the line names the wider start, the narrowing and the distance now", () => {
  const s = trailSentence(20, plan({ trail_now: 0.25, rule: { trail: 0.2, trail_start: 0.275, narrow_min: 180 } }));
  assert.match(s, /27\.5% of the entry price behind it at entry, narrowing to 20% by 180 minutes held \(25% now\)/);
  assert.doesNotMatch(s, /follows 20% of the entry price/);
});

test("before v18 (or with no plan yet) the line is the fixed trail, as before", () => {
  const fixed = "No fixed target. Once the option is up 20%, the stop moves to the entry price, then follows 20% of the entry price behind the best price reached.";
  assert.equal(trailSentence(20, null), fixed);
  assert.equal(trailSentence(20, plan({ rule: { trail: 0.2 } })), fixed);
  assert.equal(trailSentence(null, null), "No fixed target. The stop follows the best price reached.");
});
