import assert from "node:assert/strict";
import { test } from "node:test";

import { changes, problemWith, shown, typed, type ExitSetting } from "../lib/exitSettings.ts";

const trail: ExitSetting = { key: "TRAIL_FRAC", label: "Trail", low: 0.1, high: 0.5, built_in: 0.2, next_start: 0.2, minutes: false };
const late: ExitSetting = { key: "LATE_TP_AFTER_MIN", label: "Late", low: 60, high: 300, built_in: 180, next_start: 180, minutes: true };

test("fractions are shown and typed as per cent, minutes as minutes", () => {
  assert.equal(shown(trail, 0.2), "20%");
  assert.equal(shown(trail, 0.125), "12.5%");
  assert.equal(shown(late, 180), "180 min");
  assert.equal(shown(trail, null), "—");
  assert.equal(typed(trail, "25"), 0.25);
  assert.equal(typed(trail, " 22.5% "), 0.225);
  assert.equal(typed(late, "210 min"), 210);
  assert.equal(typed(trail, ""), null);
  assert.equal(typed(trail, "abc"), null);
});

test("a value outside the program's range is refused before it is sent", () => {
  assert.equal(problemWith(trail, "25"), null);
  assert.equal(problemWith(trail, "5"), "between 10% and 50%");
  assert.equal(problemWith(trail, "0.25"), "between 10% and 50%");     // 0.25% is not 25%: caught, not guessed
  assert.equal(problemWith(late, "200.5"), "whole minutes only");
  assert.equal(problemWith(late, "x"), "enter a number");
});

test("only what differs is sent", () => {
  assert.deepEqual(changes([trail, late], { TRAIL_FRAC: "25", LATE_TP_AFTER_MIN: "180" }), { TRAIL_FRAC: 0.25 });
  assert.deepEqual(changes([trail, late], { TRAIL_FRAC: "20", LATE_TP_AFTER_MIN: "180" }), {});
});
