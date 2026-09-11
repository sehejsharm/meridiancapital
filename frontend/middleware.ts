import { NextRequest, NextResponse } from "next/server";

const SESSION_COOKIE = "meridian_session";
const PUBLIC_PATHS = ["/login", "/api/session"];

export function middleware(request: NextRequest) {
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

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|icon.svg|robots.txt).*)"],
};
