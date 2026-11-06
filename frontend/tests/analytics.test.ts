/** Performance analytics (lib/analytics.ts), checked by hand against small blotters. */
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  byEntryHour, byWeekday, calendarWeeks, closedTrades, dailyPnl, kelly, monteCarlo, prng,
  rollingWinRate, underwater,
} from "../lib/analytics.ts";
import type { TradeRow } from "../lib/types.ts";

const row = (day: string, time: string, net: number, mode: "live" | "paper" = "live"): TradeRow =>
  ({ session_date: day, entry_ts: `${day} ${time}`, exit_ts: `${day} 15:00`, mode, net, net_angel: net } as unknown as TradeRow);

// 6 Nov 2026 was a Friday; 2 Nov a Monday.
const T = closedTrades([
  row("2026-11-02", "10:20", 1000),
  row("2026-11-03", "11:05", -500),
  row("2026-11-04", "10:45", -800),
  row("2026-11-06", "12:14", 3847.64),
  row("2026-11-06", "13:00", -100, "paper"),
]);

test("only real-money closed trades, oldest first", () => {
  assert.equal(T.length, 4);
  assert.deepEqual(T.map((t) => t.weekday), [0, 1, 2, 4]);
  assert.deepEqual(T.map((t) => t.hour), [10, 11, 10, 12]);
});

test("the underwater curve is the drawdown from the running peak", () => {
  const u = underwater(T);
  assert.deepEqual(u.map((p) => p.cum), [1000, 500, -300, 3547.64]);
  assert.deepEqual(u.map((p) => p.drawdown), [0, -500, -1300, 0]);
});

test("results by entry hour and by weekday", () => {
  const h = byEntryHour(T);
  assert.deepEqual(h.map((b) => [b.key, b.n, b.net]), [["10:00", 2, 200], ["11:00", 1, -500], ["12:00", 1, 3847.64]]);
  const w = byWeekday(T);
  assert.deepEqual(w.map((b) => b.key), ["Mon", "Tue", "Wed", "Fri"]);
  assert.equal(w.find((b) => b.key === "Fri")?.winRate, 1);
});

test("the calendar grid runs Monday to Friday and leaves no-trade days empty", () => {
  const weeks = calendarWeeks(dailyPnl(T));
  assert.equal(weeks.length, 1);
  assert.deepEqual(weeks[0].map((c) => c.day), ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06"]);
  assert.equal(weeks[0][3].net, null, "Thursday had no trade");
  assert.equal(weeks[0][4].net, 3847.64);
});

test("rolling win rate needs a full window", () => {
  assert.deepEqual(rollingWinRate(T, 2), [null, 0.5, 0, 0.5]);
});

test("kelly from the trailing trades, a reference only", () => {
  const many = closedTrades(Array.from({ length: 20 }, (_, i) =>
    row(`2026-10-${String(i + 1).padStart(2, "0")}`, "10:30", i % 2 ? 2000 : -1000)));
  const k = kelly(many)!;
  assert.equal(k.winRate, 0.5);
  assert.equal(k.payoff, 2);
  assert.equal(k.kelly, 0.25);   // 0.5 − 0.5 / 2
  assert.equal(k.half, 0.125);
  assert.equal(kelly(T), null, "too few trades to say");
});

test("the simulation is deterministic and counts kill switches", () => {
  const r1 = prng(7)(), r2 = prng(7)();
  assert.equal(r1, r2);
  const nets = [-13000, 2000, 2000, 2000, 2000, 2000, 2000, 2000, 2000, 2000];
  const a = monteCarlo(nets, { horizon: 20, paths: 500, dailyKill: 12000, weeklyKill: 25000, capital: 45000, seed: 1 })!;
  const b = monteCarlo(nets, { horizon: 20, paths: 500, dailyKill: 12000, weeklyKill: 25000, capital: 45000, seed: 1 })!;
  assert.deepEqual(a, b);
  // A -13,000 day is 1 in 10: over 20 sessions most paths see one (1 - 0.9^20 ≈ 88%).
  assert.ok(a.pDailyKill > 0.8 && a.pDailyKill < 0.95, String(a.pDailyKill));
  assert.ok(a.final.p50 > 0);
  assert.equal(monteCarlo([1, 2], { dailyKill: 1, weeklyKill: 1, capital: 1 }), null);
});
