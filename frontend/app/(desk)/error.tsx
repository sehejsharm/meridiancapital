"use client";

import { useEffect } from "react";

import { CrashNotice } from "@/components/CrashNotice";

/** Catches a failing page inside the shell, so the tab bar — and the route to
 *  Controls — stays on screen even when the page itself cannot render. */
export default function DeskError({
  error,
  retry,
}: {
  error: Error & { digest?: string };
  retry: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);
  return <CrashNotice error={error} retry={retry} />;
}
