/** @type {import('next').NextConfig} */

// HTTPS-only headers go out on Vercel, not on a local `next start`: telling a
// browser on http://localhost to upgrade every request would break local
// development, and HSTS is ignored over plain http anyway.
const onVercel = process.env.VERCEL === "1";

const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "no-referrer" },
  {
    key: "Permissions-Policy",
    value: "camera=(), microphone=(), geolocation=(), interest-cohort=()",
  },
];

if (onVercel) {
  securityHeaders.push(
    // Two years: after the first visit, the browser refuses to speak plain
    // http to this host at all, so a network in the middle cannot strip TLS.
    { key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains" },
    // Deliberately narrow. upgrade-insecure-requests rewrites any stray http://
    // subresource to https:// instead of loading it in the clear, and
    // frame-ancestors stops the desk being framed by another site. A policy
    // that also restricts scripts would need per-request nonces, which forces
    // every page to render dynamically; that is a larger change than this.
    { key: "Content-Security-Policy", value: "upgrade-insecure-requests; frame-ancestors 'none'" },
  );
}

const nextConfig = {
  reactStrictMode: true,
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
