import { NextRequest, NextResponse } from "next/server";

const SESSION_COOKIE = "meridian_session";
// /api/session covers its /passkey child via the startsWith check below: both
// have to answer before a session exists.
const PUBLIC_PATHS = ["/login", "/api/session"];

export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;

  if (PUBLIC_PATHS.some((p) => pathname === p || pathname.startsWith(`${p}/`))) {
    return NextResponse.next();
  }

  if (request.cookies.get(SESSION_COOKIE)) {
    return NextResponse.next();
  }

  // API relays answer with a status the client can act on; pages redirect.
  if (pathname.startsWith("/api/")) {
    return NextResponse.json({ detail: "not signed in" }, { status: 401 });
  }

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
