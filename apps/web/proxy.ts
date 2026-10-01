import { NextResponse, type NextRequest } from "next/server";

/**
 * Cookie-presence guard for the authenticated area (spec T7).
 *
 * Presence only: a stale or revoked cookie passes this and is caught by the
 * `(dashboard)` layout's `useCurrentUser` guard, which redirects to `/login`
 * on the API's 401. The API session resolver stays the authority — this is
 * UX steering, not security.
 *
 * Next.js 16 note: this file is the framework's renamed `middleware.ts` —
 * the named export is `proxy` and it runs on the nodejs runtime.
 */

const SESSION_COOKIE = "wp_session";

/** The `(dashboard)` route group's URL roots. */
const DASHBOARD_ROOTS = ["/dashboard", "/employees", "/attendance", "/command-center", "/settings"];

export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const hasSession = request.cookies.has(SESSION_COOKIE);
  const isDashboard = DASHBOARD_ROOTS.some(
    (root) => pathname === root || pathname.startsWith(`${root}/`),
  );

  if (isDashboard && !hasSession) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    url.search = "";
    return NextResponse.redirect(url);
  }

  if (pathname === "/login" && hasSession) {
    const url = request.nextUrl.clone();
    url.pathname = "/dashboard";
    url.search = "";
    return NextResponse.redirect(url);
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    "/dashboard/:path*",
    "/employees/:path*",
    "/attendance/:path*",
    "/command-center/:path*",
    "/settings/:path*",
    "/login",
  ],
};
