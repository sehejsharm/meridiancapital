/**
 * The deck's money figures (lib/account.ts). A wrong figure here is shown to
 * the operator as the account's value, so every case is pinned to the paisa.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { accountFigures, valueLabel } from "../lib/account.ts";

test("a figureless record shows as unknown, never as ₹0.00 (6 Nov 2026)", () => {
  const f = accountFigures({ equity: 0, day_pl: 0, money: null });
  assert.equal(f.value, null);
  assert.equal(f.opening, null);
  assert.equal(f.trading, null);
});

test("the program's own figures, flat: opening is equity less the day", () => {
  const f = accountFigures({ equity: 45874.92, day_pl: 3847.64, money: null });
  assert.equal(f.fromAngel, false);
  assert.equal(f.value, 45874.92);
  assert.equal(f.opening, 42027.28);
  assert.equal(f.trading, 3847.64);
  assert.equal(valueLabel(f), "Equity, the bot's figure");
});

test("a real loss to exactly zero P&L still shows its equity", () => {
  const f = accountFigures({ equity: 42027.28, day_pl: 0, money: null });
  assert.equal(f.value, 42027.28);
  assert.equal(f.trading, 0);
});

test("Angel's figures, flat and settled: balance = opening + trading + in/out", () => {
  const f = accountFigures({
    equity: 45874.92,
    day_pl: 3847.64,
    money: {
      balance: 45874.92, opening_balance: 42027.28, added: 0, day_gross: 3962.25,
      realised_gross: 3962.25, charges: 114.61, charges_estimated: false,
      equity: 45874.92, day_net: 3847.64, source: null, rms: {},
    },
  });
  assert.equal(f.fromAngel, true);
  assert.equal(f.value, 45874.92);
  assert.equal(f.trading, 3847.64);
  assert.equal(f.other, 0);
  assert.equal(valueLabel(f), "Angel balance");
  assert.equal(Math.round(((f.opening ?? 0) + (f.trading ?? 0) + (f.other ?? 0)) * 100) / 100, f.value);
});

test("no account at all is unknown", () => {
  const f = accountFigures(null);
  assert.equal(f.value, null);
});
