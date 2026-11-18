import assert from "node:assert/strict";
import { test } from "node:test";

import { blotterScope, contextNote, contextRows, type ReportContext } from "../lib/reportContext.ts";

// The figures on the owner's screens on 18 Nov 2026.
const c: ReportContext = {
  before: { trades: 13, net: 2860.32, wins: 8, win_rate_pct: 61.54, first: "2026-08-06", last: "2026-10-17" },
  period: { trades: 19, net: -8309.17, wins: 11, win_rate_pct: 57.89, first: "2026-10-19", last: "2026-11-17" },
  after: { trades: 0, net: 0, wins: 0, win_rate_pct: null, first: null, last: null },
  all: { trades: 32, net: -5448.85, wins: 19, win_rate_pct: 59.38, first: "2026-08-06", last: "2026-11-17" },
};

test("before + this report = the Blotter, in rupees and in trades", () => {
  assert.equal(Math.round((c.before.net + c.period.net) * 100) / 100, c.all.net);
  assert.equal(c.before.trades + c.period.trades, c.all.trades);
  assert.equal(c.before.wins + c.period.wins, c.all.wins);
  const rows = contextRows(c, "2026-10-19", "2026-11-18");
  assert.deepEqual(rows.map((r) => r.label), ["before", "period", "all"]);       // nothing after: no row for it
  assert.match(rows[0].text, /13 trades, \+₹2,860\.32, 8 won \(61\.5%\)/);
  assert.match(rows[1].text, /19 trades, −₹8,309\.17, 11 won \(57\.9%\)/);
  assert.match(rows[2].text, /All real-money trades, as on the Blotter: 32 trades, −₹5,448\.85, 19 won \(59\.4%\)/);
  assert.equal(rows[2].strong, true);
});

test("the note names both win rates, and is gone when the dates cover every trade", () => {
  const note = contextNote(c) ?? "";
  assert.match(note, /counts 19 of your 32/);
  assert.match(note, /57\.9%/);
  assert.match(note, /59\.4%/);
  assert.equal(contextNote({ ...c, before: { ...c.after }, period: c.all }), null);
});

test("the Blotter says what it covers", () => {
  assert.match(blotterScope(32, "2026-08-06"), /all 32 real-money trades since/);
  assert.equal(blotterScope(0, null), "No real-money trade yet.");
});
