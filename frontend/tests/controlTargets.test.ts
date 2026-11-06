/**
 * Which engine the in-session controls reach (lib/controlTargets.ts).
 * They never reached an uploaded program, and the page barely said so.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { appliesTo, controlTargets, unreachedRunning } from "../lib/controlTargets.ts";
import type { FleetAlgo } from "../lib/types.ts";

const builtin: FleetAlgo = { algo_id: "gk50k", name: "GANESH KAVACH 50K", kind: "builtin", running: false, pid: null, mode: "paper" };
const program: FleetAlgo = { algo_id: "og", name: "ganesh_kavach_og_real (3)", kind: "uploaded", running: true, pid: 42, mode: "live" };

test("only the built-in reads the commands", () => {
  const t = controlTargets([builtin, program]);
  assert.equal(t.find((x) => x.algoId === "gk50k")?.commands, true);
  assert.equal(t.find((x) => x.algoId === "og")?.commands, false);
  assert.match(t.find((x) => x.algoId === "og")?.instead ?? "", /Stop on its card/);
});

test("a live program the controls do not reach is the one to warn about", () => {
  const warn = unreachedRunning(controlTargets([builtin, program]));
  assert.deepEqual(warn.map((x) => x.algoId), ["og"]);
  assert.deepEqual(unreachedRunning(controlTargets([builtin, { ...program, running: false }])), []);
});

test("the controls say which engine they act on", () => {
  assert.equal(appliesTo(controlTargets([builtin, program])), "Applies to: GANESH KAVACH 50K (built-in) — not running, so these are off");
  assert.equal(
    appliesTo(controlTargets([{ ...builtin, running: true, mode: "live" }])),
    "Applies to: GANESH KAVACH 50K (built-in), on real money",
  );
});
