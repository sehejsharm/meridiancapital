/** One phrase for the gate on every page (lib/gate.ts). */
import { test } from "node:test";
import assert from "node:assert/strict";

import { gateText } from "../lib/gate.ts";

test("an uploaded full program reads the same on the list and the detail page", () => {
  assert.equal(gateText("program"), "full program · gate n/a");
  assert.equal(gateText("none", "program"), "full program · gate n/a");
});

test("screened strategies and the built-in", () => {
  assert.equal(gateText("passed"), "gate passed");
  assert.equal(gateText("failed"), "gate flagged issues");
  assert.equal(gateText("none"), "gate not run");
  assert.equal(gateText("none", "builtin"), "built-in engine");
});
