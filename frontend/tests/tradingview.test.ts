/** The TradingView tab never draws a chart of the wrong instrument (it showed AAPL for NSE:NIFTY). */
import { test } from "node:test";
import assert from "node:assert/strict";

import { isEmbeddable, tradingViewUrl } from "../components/tvSymbols.ts";

test("NSE and BSE symbols are linked out, never embedded", () => {
  assert.equal(isEmbeddable("NSE:NIFTY"), false);
  assert.equal(isEmbeddable("bse:SENSEX"), false);
  assert.equal(isEmbeddable("NASDAQ:AAPL"), true);
});

test("the link opens the index itself", () => {
  assert.equal(tradingViewUrl("NSE:NIFTY"), "https://www.tradingview.com/chart/?symbol=NSE%3ANIFTY");
});
