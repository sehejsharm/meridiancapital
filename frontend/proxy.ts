import { NextRequest, NextResponse } from "next/server";

import { openSession, sessionCookieName, staleSessionCookieNames } from "./lib/session-cookie";

// /api/session covers its /passkey and /claim children via the startsWith check
// below: they have to answer before a session exists.
// /sw.js and /offline must answer without a session: the service worker is fetched by the
// browser on its own schedule, and the offline page is stored before anyone is signed in.
const PUBLIC_PATHS = ["/login", "/api/session", "/offline", "/sw.js"];

/**
 * The door to the desk. No page, and no relayed call, is served without a
 * session cookie this deployment sealed and that has not expired — a made-up or
 * altered cookie is turned away here, before anything renders. (The control
 * plane checks the token inside again on every call, along with its device.)
 */
export async function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;

  if (PUBLIC_PATHS.some((p) => pathname === p || pathname.startsWith(`${p}/`))) {
    return NextResponse.next();
  }

  const held = request.cookies.get(sessionCookieName())?.value;
  if (await openSession(held)) {
    return NextResponse.next();
  }

  // API relays answer with a status the client can act on; pages redirect.
  const response = pathname.startsWith("/api/")
    ? NextResponse.json({ detail: "not signed in" }, { status: 401 })
    : redirectToLogin(request, pathname);
  // A cookie that failed the check is worthless: drop it rather than send it again.
  for (const name of [sessionCookieName(), ...staleSessionCookieNames()]) {
    if (request.cookies.get(name)) {
      response.cookies.set({ name, value: "", path: "/", maxAge: 0, httpOnly: true, secure: name.startsWith("__Host-") || request.nextUrl.protocol === "https:", sameSite: "lax" });
    }
  }
  return response;
}

function redirectToLogin(request: NextRequest, pathname: string) {
  const target = request.nextUrl.clone();
  target.pathname = "/login";
  target.search = pathname === "/" ? "" : `?next=${encodeURIComponent(pathname)}`;
  return NextResponse.redirect(target);
}

// Everything a browser or home screen fetches *without* the session cookie has
// to bypass the sign-in redirect. A manifest is requested with credentials
// omitted, and iOS fetches the touch icon the same way — redirected to /login
// they receive HTML instead of an image, and the installed app shows a letter
// instead of the logo. iOS also probes /apple-touch-icon.png and
// /apple-touch-icon-precomposed.png at the root on its own, whatever the page
// declares, so any static image or manifest by extension is let through too.
export const config = {
  matcher: [
    "/((?!_next/static|_next/image|icons/|.*\\.(?:png|ico|svg|webmanifest|txt)$).*)",
  ],
};
