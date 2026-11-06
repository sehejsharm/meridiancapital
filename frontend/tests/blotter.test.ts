/** The blotter's reconciliation counts and marks (lib/blotter.ts) agree, row for row. */
import { test } from "node:test";
import assert from "node:assert/strict";

import { matchLine, matchOf } from "../lib/blotter.ts";
import type { TradeRow, TradeStats } from "../lib/types.ts";

const t = (o: Partial<TradeRow>) => o as TradeRow;

test("a fund-transfer session outranks a tick, as the row shows it", () => {
  assert.equal(matchOf(t({ reconciled: "Angel balance", ledger_gap: -47841.0 })), "fund_transfer");
  assert.equal(matchOf(t({ reconciled: "Angel ledger", ledger_gap: null })), "matched");
  assert.equal(matchOf(t({ reconciled: "recorded" })), "provisional");
  assert.equal(matchOf(t({ match: "matched", ledger_gap: 5 })), "matched", "the server's word wins");
});

test("the header counts each category once (16/19 Oct 2026)", () => {
  const stats = { n: 27, reconciled: 25, fund_transfer: 2, provisional: 0 } as TradeStats;
  assert.equal(matchLine(stats), "25 of 27 matched to Angel · 2 on fund-transfer days");
  assert.equal(
    matchLine({ n: 3, reconciled: 1, fund_transfer: 1, provisional: 1 } as TradeStats),
    "1 of 3 matched to Angel · 1 on fund-transfer days · 1 provisional",
  );
});
