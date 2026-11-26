import { execSync } from "node:child_process";

import { backendFingerprint } from "./backend-fingerprint.mjs";

// HTTPS-only headers go out on Vercel, not on a local `next start`: telling a
// browser on http://localhost to upgrade every request would break local
// development, and HSTS is ignored over plain http anyway.
const onVercel = process.env.VERCEL === "1";
const isDev = process.env.NODE_ENV === "development";

// The one place the browser talks to besides this site: the live feed's
// WebSocket, straight to the control plane (Vercel cannot hold a socket open).
function socketOrigin() {
  try {
    const url = new URL(process.env.MERIDIAN_API_URL ?? "");
    return `${url.protocol === "https:" ? "wss:" : "ws:"}//${url.host}`;
  } catch {
    return null;
  }
}
const socket = socketOrigin();

// What a page of the desk may load and talk to. Everything comes from this site
// except the live socket; no third-party script, frame, font or image at all,
// so even an injected script would have nowhere to send what it read.
// 'unsafe-inline' scripts: Next's own inline bootstrap needs it without per-request
// nonces, which would force every page to render dynamically.
const contentSecurityPolicy = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self'",
  `connect-src 'self'${socket ? ` ${socket}` : " wss:"}`,
  "worker-src 'self'",
  "manifest-src 'self'",
  "media-src 'none'",
  "frame-src 'none'",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'self'",
  "frame-ancestors 'none'",
  ...(onVercel ? ["upgrade-insecure-requests"] : []),
].join("; ");

const securityHeaders = [
  { key: "Content-Security-Policy", value: contentSecurityPolicy },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "no-referrer" },
  // A page opened from the desk cannot reach back into it, and nothing outside
  // can embed the desk's responses.
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
  { key: "Cross-Origin-Resource-Policy", value: "same-origin" },
  { key: "X-Robots-Tag", value: "noindex, nofollow, noarchive" },
  { key: "X-DNS-Prefetch-Control", value: "off" },
  { key: "X-Permitted-Cross-Domain-Policies", value: "none" },
  {
    key: "Permissions-Policy",
    value:
      "camera=(), microphone=(), geolocation=(), payment=(), usb=(), serial=(), bluetooth=(), hid=(), " +
      "midi=(), magnetometer=(), gyroscope=(), accelerometer=(), display-capture=(), browsing-topics=()",
  },
];

if (onVercel) {
  // Two years: after the first visit, the browser refuses to speak plain
  // http to this host at all, so a network in the middle cannot strip TLS.
  securityHeaders.push({ key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains" });
}

// The commit this build is from, shown beside the server's on the deck so an
// out-of-date server is plain to see. Vercel supplies it; a local build asks git.
function buildCommit() {
  if (process.env.VERCEL_GIT_COMMIT_SHA) return process.env.VERCEL_GIT_COMMIT_SHA.slice(0, 7);
  try {
    return execSync("git rev-parse --short=7 HEAD", { stdio: ["ignore", "pipe", "ignore"] })
      .toString()
      .trim();
  } catch {
    return "";
  }
}

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  env: {
    NEXT_PUBLIC_BUILD_COMMIT: buildCommit(),
    // The server's code as of this build, to tell an outdated server apart
    // from one that simply had nothing new in a dashboard-only change.
    NEXT_PUBLIC_BACKEND_FINGERPRINT: backendFingerprint(),
  },
  poweredByHeader: false,
  async headers() {
    return [
      { source: "/:path*", headers: securityHeaders },
      {
        // Icons are named for their content and rebuilt only by
        // scripts/build-icons.mjs, so a day's caching is safe and saves a
        // round trip on every cold open of the installed app.
        source: "/icons/:file*",
        headers: [{ key: "Cache-Control", value: "public, max-age=86400, stale-while-revalidate=604800" }],
      },
    ];
  },
};

export default nextConfig;
