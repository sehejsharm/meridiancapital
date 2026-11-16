import assert from "node:assert/strict";
import { test } from "node:test";

import { describePlan } from "../lib/exitPlan.ts";
import { serverLines } from "../lib/server.ts";
import { minuteOfDay, polyline, range } from "../lib/tradePath.ts";
import type { ExitPlan, HostReport } from "../lib/types.ts";

const plan: ExitPlan = {
  stage: "initial stop", stop_gain: -0.45, stop_price: 110, locked_in: 0, peak_gain: 0.05,
  steps: [], next: { key: "break_even", label: "Break-even", at_gain: 0.2, at_price: 240, reached: false, effect: "the stop moves up to the entry price" },
  late_tp: { after_min: 180, at_gain: 0.4, at_price: 280, minutes_left: 150, on: false }, force_close: "15:10",
};

test("a fresh trade: the stop, the next step and how far away it is", () => {
  const v = describePlan(plan, 210, null);
  assert.equal(v.stop, "₹110.00");
  assert.equal(v.stopNote, "-45% from entry");
  assert.equal(v.next, "Break-even at ₹240.00 (+20%): the stop moves up to the entry price");
  assert.equal(v.nextDistance, "₹30.00 away (+14.3%)");            // 30 / 210
  assert.equal(v.late, "Late take-profit switches on in 150 min: then sells at ₹280.00 (+40%) or better");
  assert.equal(v.close, "Closed at 15:10 whatever happens");
});

test("the resting order at Angel is shown when it differs from the rule's level", () => {
  const v = describePlan(plan, 210, 112.5);
  assert.equal(v.stop, "₹112.50");
  assert.equal(v.stopNote, "resting at Angel; the rules put it at ₹110.00");
  assert.equal(describePlan(plan, 210, 110.02).stop, "₹110.00");     // within a tick: the same stop
});

test("a trailing trade says what is locked in, and a switched-on late take-profit says so", () => {
  const t: ExitPlan = { ...plan, stage: "trailing", stop_gain: 0.15, stop_price: 230, locked_in: 0.15, next: null,
    late_tp: { after_min: 180, at_gain: 0.4, at_price: 280, minutes_left: 0, on: true } };
  const v = describePlan(t, 262, 230);
  assert.equal(v.stopNote, "locks in +15%");
  assert.equal(v.next, null);
  assert.equal(v.late, "Late take-profit is on: sells at ₹280.00 (+40%) or better");
});

const host: HostReport = {
  machine: { cpu: { cores: 1, load_5m: 0.3, load_pct: 30 }, memory: { total_mb: 1024, used_mb: 600, used_pct: 58.6 },
    disk: { total_gb: 45, free_gb: 6.7, free_pct: 15 }, storage: { database_mb: 40, backups_mb: 300, archive_mb: null, research_mb: 2 },
    uptime_hours: 100, warnings: [{ kind: "disk", level: "warning", text: "15% of the disk is free" }] },
  upkeep: {
    heartbeat: { configured: false, last_ok: null, last_try: null, last_error: null },
    close_check: { day: "2026-11-13", ran_at: "2026-11-13T15:36:00", status: "ok", trades: 1, angel: "asked", independent: true, findings: [] },
    angel: { configured: true, enabled: true }, journal_keep_days: 365,
    backups: { backup: { ts: "x", file: "f", bytes: 1, off_machine: "not set up", age_hours: 11 }, restore_test: null, problems: [], recorded: true },
  },
};

test("each housekeeping line has the right tone", () => {
  const by = Object.fromEntries(serverLines(host).map((l) => [l.key, l]));
  assert.equal(by.disk.tone, "warning");
  assert.equal(by.disk.text, "15% free (6.7 of 45 GB); warns below 20%");
  assert.equal(by.cpu.tone, "good");
  assert.equal(by.heartbeat.tone, "warning");
  assert.match(by.heartbeat.text, /not set up/);
  assert.equal(by.backup.text, "11 hours ago, kept on the VM only");
  assert.equal(by.restore.tone, "warning");
  assert.equal(by.close.text, "2026-11-13: flat, 1 real-money trade agree with Angel's books");
});

test("a failed restore test and a problem at the close are critical", () => {
  const bad: HostReport = JSON.parse(JSON.stringify(host));
  bad.upkeep!.backups.restore_test = { ts: "x", ok: false, detail: "integrity check: bad" };
  bad.upkeep!.close_check = { ...host.upkeep!.close_check!, status: "problem", findings: [
    { level: "critical", kind: "position_open", text: "Angel shows NIFTY17NOV2625500CE still open: +65 qty" }, { level: "warn", kind: "x", text: "y" }] };
  bad.upkeep!.heartbeat = { configured: true, last_ok: null, last_try: "x", last_error: "TimeoutError" };
  const by = Object.fromEntries(serverLines(bad).map((l) => [l.key, l]));
  assert.equal(by.restore.tone, "critical");
  assert.equal(by.close.tone, "critical");
  assert.equal(by.close.text, "2026-11-13: Angel shows NIFTY17NOV2625500CE still open: +65 qty (+1 more)");
  assert.equal(by.heartbeat.tone, "critical");
});

test("the trade chart scales time and price into the box", () => {
  assert.equal(minuteOfDay("10:30"), 630);
  assert.equal(minuteOfDay("2026-11-11T10:30:30"), 630.5);
  assert.equal(polyline([{ t: 630, v: 100 }, { t: 660, v: 150 }, { t: 690, v: 125 }], 630, 690, 100, 150, 600, 200), "0.0,200.0 300.0,0.0 600.0,100.0");
  assert.deepEqual(range([100, 150]), [97, 153]);
  assert.deepEqual(range([]), [0, 1]);
});
