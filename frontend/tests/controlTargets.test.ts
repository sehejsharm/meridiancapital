/**
 * What the in-session controls do to each engine (lib/controlTargets.ts).
 * They used to reach only the built-in engine; an uploaded program now gets
 * Option A (7 Nov 2026): Flatten stops it (it sells), Halt stops it only while flat.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { appliesTo, controlTargets, defaultTarget } from "../lib/controlTargets.ts";
import type { FleetAlgo } from "../lib/types.ts";

const builtin: FleetAlgo = { algo_id: "gk50k", name: "GANESH KAVACH 50K", kind: "builtin", running: false, pid: null, mode: "paper" };
const program: FleetAlgo = { algo_id: "og", name: "ganesh_kavach_og_real (3)", kind: "uploaded", running: true, pid: 42, mode: "live" };

test("the controls open on the engine trading real money", () => {
  assert.equal(defaultTarget(controlTargets([builtin, program])), "og");
  assert.equal(defaultTarget(controlTargets([builtin, { ...program, running: false }])), "gk50k");
});

test("a program's controls say exactly what they do to it", () => {
  const og = controlTargets([builtin, program]).find((t) => t.algoId === "og")!;
  assert.match(og.flatten, /sells any open position/);
  assert.match(og.halt, /only while it is flat/);
  assert.equal(og.clearWeek, null, "not available for a program");
  const gk = controlTargets([builtin]).find((t) => t.algoId === "gk50k")!;
  assert.notEqual(gk.clearWeek, null);
});

test("the card names the engine it acts on", () => {
  const [gk, og] = controlTargets([builtin, program]);
  assert.equal(appliesTo(og), "Applies to: ganesh_kavach_og_real (3) (uploaded program) — running on real money");
  assert.equal(appliesTo(gk), "Applies to: GANESH KAVACH 50K (built-in engine) — not running");
});
