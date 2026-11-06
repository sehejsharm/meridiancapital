/** Session replay's timeline (lib/replay.ts). */
import { test } from "node:test";
import assert from "node:assert/strict";

import { barMinute, clock, minuteOf, openAt, valueAt } from "../lib/replay.ts";

test("timestamps land on the 09:15-based timeline", () => {
  assert.equal(minuteOf("2026-11-06T09:15:00"), 0);
  assert.equal(minuteOf("2026-11-06 12:14"), 179);
  assert.equal(minuteOf("2026-11-06T15:30:00"), 375);
  assert.equal(minuteOf(null), null);
  assert.equal(clock(179), "12:14");
  // A chart bar for 12:14 IST (epoch already shifted to IST).
  assert.equal(barMinute(Date.UTC(2026, 10, 6, 12, 14) / 1000), 179);
});

test("the value at a minute is the last one recorded by then", () => {
  const s = [{ minute: 0, value: 0 }, { minute: 179, value: 120 }, { minute: 241, value: 3847.64 }];
  assert.equal(valueAt(s, 200), 120);
  assert.equal(valueAt(s, 241), 3847.64);
  assert.equal(valueAt(s, -1), null);
});

test("a trade is open from its entry minute until its exit minute", () => {
  const t = [{ entry: 179, exit: 241 }];
  assert.equal(openAt(t, 178).length, 0);
  assert.equal(openAt(t, 179).length, 1);
  assert.equal(openAt(t, 241).length, 0);
});
