/**
 * Checks on requests that reach the desk's route handlers. Pure functions, so the
 * tests can import them.
 */

/**
 * The request was made by this site's own pages, not by another site the operator
 * happens to have open. Required for everything that changes something: a page on
 * another site can make the browser send a POST here (with the session cookie on
 * a top-level form), but it cannot make the browser say the request is same-origin.
 */
export function sameOrigin(headers: Headers): boolean {
  const site = headers.get("sec-fetch-site");
  if (site && site !== "same-origin") return false;
  const origin = headers.get("origin");
  if (!origin) {
    // No Origin on a state-changing request: not a browser page making it (they
    // always send one), unless Fetch Metadata said otherwise above.
    return true;
  }
  let originHost: string;
  try {
    originHost = new URL(origin).host;
  } catch {
    return false;
  }
  const hosts = [headers.get("host"), headers.get("x-forwarded-host")]
    .flatMap((h) => (h ?? "").split(","))
    .map((h) => h.trim())
    .filter(Boolean);
  return hosts.includes(originHost);
}

/**
 * Where to go after signing in: a path on this site, nothing else. "/trades" yes;
 * "//evil.example", "/\\evil.example" and "https://evil.example" (which a browser
 * would follow off-site) no.
 */
export function safeNext(next: string | null | undefined): string {
  if (!next || next.length > 2048) return "/";
  if (!/^\/(?![/\\])/.test(next) || /[\u0000-\u001f\\]/.test(next)) return "/";
  try {
    const url = new URL(next, "https://desk.invalid");
    if (url.origin !== "https://desk.invalid") return "/";
    return `${url.pathname}${url.search}${url.hash}`;
  } catch {
    return "/";
  }
}

/** "iPhone · Safari": how a device is named in the approval list. */
export function deviceLabel(userAgent: string | null | undefined): string {
  const ua = userAgent ?? "";
  const platform = /iPhone/.test(ua)
    ? "iPhone"
    : /iPad/.test(ua)
      ? "iPad"
      : /Android/.test(ua)
        ? /Mobile/.test(ua)
          ? "Android phone"
          : "Android tablet"
        : /CrOS/.test(ua)
          ? "Chromebook"
          : /Macintosh|Mac OS X/.test(ua)
            ? "Mac"
            : /Windows/.test(ua)
              ? "Windows PC"
              : /Linux/.test(ua)
                ? "Linux PC"
                : "Unknown device";
  const browser = /Edg(e|A|iOS)?\//.test(ua)
    ? "Edge"
    : /OPR\/|Opera/.test(ua)
      ? "Opera"
      : /SamsungBrowser/.test(ua)
        ? "Samsung Internet"
        : /Firefox\/|FxiOS/.test(ua)
          ? "Firefox"
          : /CriOS|Chrome\//.test(ua)
            ? "Chrome"
            : /Safari\//.test(ua)
              ? "Safari"
              : "browser";
  return `${platform} · ${browser}`;
}
