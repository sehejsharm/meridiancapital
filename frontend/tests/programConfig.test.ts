/**
 * The Strategy page's display of a program's configuration (lib/programConfig.ts).
 * 6 Nov 2026: the page showed the built-in engine's 25% / 35% / 90 points as if
 * they were the live program's 20% / 20% / none.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { builtinDifferences, driftSummary, effectiveValue, formatParam } from "../lib/programConfig.ts";
import type { ProgramConfigReport, ProgramConfigRow } from "../lib/types.ts";

const row = (o: Partial<ProgramConfigRow>): ProgramConfigRow => ({
  key: "BE_TRIGGER", label: "Break-even trigger", kind: "pct",
  running: 0.2, running_known: true, code: 0.2, code_known: true,
  builtin: 0.25, builtin_known: true, drift: false, differs_from_builtin: true, ...o,
});

const report = (rows: ProgramConfigRow[]): ProgramConfigReport => ({
  algo_id: "og", name: "ganesh_kavach_og_real (3)", mode: "live",
  running: { logged_ts: "2026-11-06T09:06:41", event_id: 1 }, code: { version: 8, sha256: "abc" },
  rows, drift: rows.filter((r) => r.drift).map((r) => r.key),
  differs_from_builtin: rows.filter((r) => r.differs_from_builtin).map((r) => r.key),
});

test("parameters read the way the program logs them", () => {
  assert.equal(formatParam("pct", 0.2), "20%");
  assert.equal(formatParam("pct", 0.025), "2.5%");
  assert.equal(formatParam("money", 9000), "₹9,000");
  assert.equal(formatParam("time", "10:15"), "10:15");
  assert.equal(formatParam("int", 90), "90");
});

test("no take-profit reads as 'none', not as missing", () => {
  assert.equal(formatParam("int", null), "none");
  assert.equal(formatParam("int", undefined), "—");
});

test("what the program uses: its startup log first, its code if it never started", () => {
  assert.equal(effectiveValue(row({ running: 0.3, code: 0.2 })), 0.3);
  assert.equal(effectiveValue(row({ running_known: false, running: null, code: 0.2 })), 0.2);
});

test("the three values that differed from the built-in on 6 Nov are named", () => {
  const got = builtinDifferences(report([
    row({}),
    row({ key: "TRAIL_FRAC", label: "Trail start", running: 0.2, code: 0.2, builtin: 0.35 }),
    row({ key: "TARGET_PTS", label: "Take-profit", kind: "int", running: null, code: null, builtin: 90 }),
    row({ key: "STOP_FRAC", label: "Hard stop", running: 0.45, code: 0.45, builtin: 0.45, differs_from_builtin: false }),
  ]));
  assert.deepEqual(got, [
    "Break-even trigger: 20% (built-in 25%)",
    "Trail start: 20% (built-in 35%)",
    "Take-profit: none (built-in 90)",
  ]);
});

test("drift names what the process runs against what its code says", () => {
  assert.equal(driftSummary(report([row({})])), null);
  assert.equal(
    driftSummary(report([row({ running: 0.2, code: 0.25, drift: true })])),
    "ganesh_kavach_og_real (3): Break-even trigger 20% running vs 25% in its code",
  );
});
