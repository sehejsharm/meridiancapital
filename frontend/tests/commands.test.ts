/** The command palette (lib/commands.ts): navigation, and no trading action fired from a search box. */
import { test } from "node:test";
import assert from "node:assert/strict";

import { algoCommands, baseCommands, matchCommands } from "../lib/commands.ts";

const all = [...baseCommands("2026-11-06"), ...algoCommands([{ id: "og", name: "ganesh_kavach_og_real (3)", running: true }])];

test("typing narrows to what matches every word", () => {
  assert.deepEqual(matchCommands(all, "jour").map((c) => c.id), ["journal-today"]);
  assert.deepEqual(matchCommands(all, "halt").map((c) => c.id), ["controls"]);
  assert.equal(matchCommands(all, "").length, all.length);
});

test("start or stop opens the algorithm's page, it never acts", () => {
  const [c] = matchCommands(all, "stop ganesh");
  assert.equal(c.label, "Stop ganesh_kavach_og_real (3)…");
  assert.equal(c.href, "/algos/og");
});
