"use client";

import "./globals.css";

import { CrashNotice } from "@/components/CrashNotice";

/** Last resort, for a failure in the root layout itself. Replaces the whole
 *  document, so it brings its own html, body and stylesheet. */
export default function GlobalError({
  error,
  retry,
}: {
  error: Error & { digest?: string };
  retry: () => void;
}) {
  return (
    <html lang="en">
      <body className="px-4">
        <title>Meridian Capital — error</title>
        <CrashNotice error={error} retry={retry} standalone />
      </body>
    </html>
  );
}
