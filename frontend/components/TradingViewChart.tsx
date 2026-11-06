"use client";

import { useEffect, useRef, useState } from "react";

import { isEmbeddable, tradingViewUrl } from "@/components/tvSymbols";

/**
 * TradingView's Advanced Chart, embedded for live NIFTY price action.
 *
 * This is their hosted widget: real exchange data, their indicators, their
 * drawing tools. It renders inside an iframe the widget script creates, so
 * nothing here can read from it — trade markers belong on the companion chart,
 * which we draw ourselves from our own fills.
 *
 * The script is third-party and can be blocked (an ad blocker, a corporate
 * proxy, no network). That is a normal outcome, not an error, so it degrades
 * to a labelled placeholder with a link out rather than an empty box.
 */
export function TradingViewChart(props: { symbol?: string; interval?: string; height?: number }) {
  const symbol = props.symbol ?? "NSE:NIFTY";
  if (!isEmbeddable(symbol)) return <LinkOut symbol={symbol} height={props.height ?? 420} />;
  return <EmbeddedChart symbol={symbol} interval={props.interval ?? "5"} height={props.height ?? 420} />;
}

function LinkOut({ symbol, height }: { symbol: string; height: number }) {
  return (
    <div
      className="flex h-[300px] flex-col items-center justify-center gap-3 rounded-md border border-dashed border-hairline bg-surface px-6 text-center sm:h-[var(--chart-h)]"
      style={{ ["--chart-h" as string]: `${height}px` }}
    >
      <p className="max-w-md text-xs leading-relaxed text-ink-secondary">
        NSE licenses its prices to TradingView for tradingview.com only, so TradingView&apos;s embedded chart
        cannot show {symbol} here — it would quietly draw a different instrument instead. The Chart tab draws
        NIFTY 50 from Angel One&apos;s own candles.
      </p>
      <a
        href={tradingViewUrl(symbol)}
        target="_blank"
        rel="noreferrer noopener"
        className="inline-flex items-center rounded-md border border-brand/50 bg-brand-dim px-3 py-2 text-xs font-semibold uppercase tracking-[0.1em] text-brand transition-colors hover:border-brand touch:min-h-[44px]"
      >
        Open {symbol} on TradingView ↗
      </a>
    </div>
  );
}

function EmbeddedChart({ symbol, interval, height }: { symbol: string; interval: string; height: number }) {
  const holder = useRef<HTMLDivElement | null>(null);
  const [failed, setFailed] = useState(false);
  // The embed is several hundred KB of third-party script plus an iframe. On a
  // phone it sits three screens below the fold, so it waits until it is about
  // to be seen instead of competing with the numbers you opened the deck for.
  const [near, setNear] = useState(false);

  useEffect(() => {
    const node = holder.current;
    if (!node || near) return;
    if (typeof IntersectionObserver === "undefined") {
      setNear(true);
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setNear(true);
          io.disconnect();
        }
      },
      { rootMargin: "400px 0px" },
    );
    io.observe(node);
    return () => io.disconnect();
  }, [near]);

  useEffect(() => {
    const node = holder.current;
    if (!node || !near) return;
    node.innerHTML = "";

    const container = document.createElement("div");
    container.className = "tradingview-widget-container__widget";
    container.style.height = "100%";
    node.appendChild(container);

    const script = document.createElement("script");
    script.src =
      "https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js";
    script.async = true;
    script.onerror = () => setFailed(true);
    script.innerHTML = JSON.stringify({
      symbol,
      interval,
      timezone: "Asia/Kolkata",
      theme: "dark",
      style: "1",
      locale: "en",
      hide_side_toolbar: true,
      allow_symbol_change: false,
      save_image: false,
      backgroundColor: "rgba(0,0,0,0)",
      support_host: "https://www.tradingview.com",
    });
    node.appendChild(script);

    // The widget replaces the container asynchronously; if nothing has been
    // injected after a grace period, treat it as blocked.
    const timer = setTimeout(() => {
      if (!container.querySelector("iframe")) setFailed(true);
    }, 6000);

    return () => {
      clearTimeout(timer);
      node.innerHTML = "";
    };
  }, [symbol, interval, near]);

  return (
    <div
      className="relative h-[300px] overflow-hidden rounded-md sm:h-[var(--chart-h)]"
      style={{ ["--chart-h" as string]: `${height}px` }}
    >
      <div ref={holder} className="h-full w-full" />
      {failed && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 rounded-md border border-dashed border-hairline bg-surface px-4 text-center">
          <p className="text-xs text-ink-secondary">
            TradingView&apos;s chart could not load — it is blocked or unreachable from here.
          </p>
          <a
            href={tradingViewUrl(symbol)}
            target="_blank"
            rel="noreferrer noopener"
            className="text-xs font-medium text-brand underline underline-offset-2"
          >
            Open {symbol} on tradingview.com
          </a>
        </div>
      )}
    </div>
  );
}
