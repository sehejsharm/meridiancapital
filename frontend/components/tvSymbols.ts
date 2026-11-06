/**
 * Exchanges whose data TradingView shows only on tradingview.com, never inside
 * an embedded widget. Asked for NSE:NIFTY, the widget showed "This symbol is
 * only available on TradingView" and then drew its default symbol — an Apple
 * (AAPL) chart under a card titled NIFTY 50. A chart of the wrong instrument is
 * worse than none, so these symbols get a labelled link out instead.
 */
const NOT_EMBEDDABLE = /^(NSE|BSE):/i;

export function isEmbeddable(symbol: string): boolean {
  return !NOT_EMBEDDABLE.test(symbol.trim());
}

export function tradingViewUrl(symbol: string): string {
  return `https://www.tradingview.com/chart/?symbol=${encodeURIComponent(symbol)}`;
}
