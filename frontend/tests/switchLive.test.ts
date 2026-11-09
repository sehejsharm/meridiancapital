/** The algorithm page's Switch button (lib/switchLive.ts). */
import { test } from "node:test";
import assert from "node:assert/strict";

import { switchPlan } from "../lib/switchLive.ts";
import type { FleetAlgo } from "../lib/types.ts";

const og: FleetAlgo = { algo_id: "og", name: "ganesh_kavach_og_real (3)", kind: "uploaded", running: true, pid: 42, mode: "live" };
const v9: FleetAlgo = { algo_id: "v9", name: "ganesh_kavach_v9", kind: "uploaded", running: false, pid: null, mode: "paper" };

test("offered when another algorithm trades real money", () => {
  const p = switchPlan([og, v9], "v9", "ganesh_kavach_v9")!;
  assert.equal(p.button, "Switch real money from ganesh_kavach_og_real (3)");
  assert.match(p.confirm, /sells any open position/);
  assert.match(p.confirm, /starts ganesh_kavach_v9 on real money/);
});

test("not offered when nothing else is live, or this one already is", () => {
  assert.equal(switchPlan([{ ...og, mode: "paper" }, v9], "v9", "v9"), null);
  assert.equal(switchPlan([og, v9], "og", "og"), null);
  assert.equal(switchPlan(undefined, "v9", "v9"), null);
});

test("a paper run of this one can still take over", () => {
  assert.notEqual(switchPlan([og, { ...v9, running: true }], "v9", "v9"), null);
});
