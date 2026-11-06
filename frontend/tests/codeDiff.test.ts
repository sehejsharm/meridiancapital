/** The code viewer's helpers (lib/codeDiff.ts). The redaction itself is tested server-side. */
import { test } from "node:test";
import assert from "node:assert/strict";

import { defaultPair, hunkHeader, isHidden } from "../lib/codeDiff.ts";
import type { AlgoVersion } from "../lib/types.ts";

const v = (id: number, version: number): AlgoVersion =>
  ({ id, algo_id: "og", version, created_ts: "", uploaded_by: null, sha256: "", status: "program", gate_report: null });

test("the newest upload against the one before it", () => {
  assert.deepEqual(defaultPair([v(12, 8), v(11, 7), v(4, 3)]), { a: 11, b: 12 });
  assert.deepEqual(defaultPair([v(4, 3), v(12, 8), v(11, 7)]), { a: 11, b: 12 }, "whatever order they arrive in");
  assert.equal(defaultPair([v(12, 8)]), null);
});

test("hunk headers read like a unified diff", () => {
  assert.equal(hunkHeader({ a_start: 12, a_len: 7, b_start: 12, b_len: 8, lines: [] }), "@@ -12,7 +12,8 @@");
});

test("blanked lines are recognised", () => {
  assert.ok(isHidden("    # ··· credential line hidden ···"));
  assert.ok(!isHidden("STOP_FRAC = 0.45"));
});
