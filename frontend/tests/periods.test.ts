/** Tear-sheet weeks and tax years (lib/periods.ts), matching the backend's. */
import { test } from "node:test";
import assert from "node:assert/strict";

import { fyOf, periodLabel, weekStart } from "../lib/periods.ts";

test("a week starts on Monday", () => {
  assert.equal(weekStart("2026-11-06"), "2026-11-02"); // a Friday
  assert.equal(weekStart("2026-11-02"), "2026-11-02");
  assert.equal(weekStart("2026-11-08"), "2026-11-02"); // Sunday belongs to the week before it
  assert.equal(weekStart("2027-01-01"), "2026-12-28");
});

test("the label matches the PDF's", () => {
  assert.equal(periodLabel("week", "2026-11-06"), "Week of 2 Nov 2026");
  assert.equal(periodLabel("month", "2026-11-06"), "November 2026");
});

test("financial years run April to March", () => {
  assert.equal(fyOf("2026-11-06"), "2026-27");
  assert.equal(fyOf("2026-03-31"), "2025-26");
  assert.equal(fyOf("2026-04-01"), "2026-27");
  assert.equal(fyOf("2099-12-01"), "2099-00");
});
