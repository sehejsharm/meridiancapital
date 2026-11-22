import assert from "node:assert/strict";
import { test } from "node:test";

import { betaWord, channelPosition, trail, trendWord } from "../lib/deskMetrics.ts";

test("NIFTY's place in its channel, in numbers and words", () => {
  assert.equal(channelPosition(25350, 25300, 25400), 50);
  assert.equal(channelPosition(25420, 25300, 25400), 120);
  assert.equal(channelPosition(null, 1, 2), null);
  assert.equal(channelPosition(1, 5, 5), null);
  assert.equal(trendWord(120), "Broke out up");
  assert.equal(trendWord(-5), "Broke out down");
  assert.equal(trendWord(80), "Near the high");
  assert.equal(trendWord(50), "Mid-channel");
});

test("beta in words", () => {
  assert.equal(betaWord(0.05), "barely moves with NIFTY");
  assert.equal(betaWord(0.4), "moves partly with NIFTY");
  assert.equal(betaWord(-0.9), "moves against NIFTY");
  assert.equal(betaWord(null), "—");
});

test("the trail: stop, what it locks in, room to it", () => {
  const t = trail(300, 250, 200)!;
  assert.equal(t.stop, 250);
  assert.equal(t.locked, 0.25);
  assert.equal(Math.round(t.room! * 1000) / 1000, 0.167);
  assert.equal(trail(null, 1, 1), null);
});
