"use client";

import { useEffect, useRef } from "react";

import { useLiveFeed } from "@/lib/LiveContext";
import { LAST_FIGURES_KEY, type LastFigures } from "@/lib/phone";

const SAVE_EVERY_MS = 10_000;

/** Registers the service worker and keeps the last figures for the offline page. Renders nothing. */
export function PhoneKeeper() {
  const { snapshot } = useLiveFeed();
  const savedAt = useRef(0);

  useEffect(() => {
    if (!("serviceWorker" in navigator)) return;
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!snapshot || Date.now() - savedAt.current < SAVE_EVERY_MS) return;
    savedAt.current = Date.now();
    const figures: LastFigures = {
      ts: new Date().toISOString(),
      day_pl: snapshot.account?.day_pl ?? null,
      equity: snapshot.account?.equity ?? null,
      position: snapshot.position?.tsym ?? null,
      unrealised: snapshot.position?.unrealised ?? null,
    };
    try {
      window.localStorage.setItem(LAST_FIGURES_KEY, JSON.stringify(figures));
    } catch {
      /* private mode or full storage: the offline page simply has nothing to show */
    }
  }, [snapshot]);

  return null;
}
