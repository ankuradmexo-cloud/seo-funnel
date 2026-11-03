import { NextRequest, NextResponse } from "next/server";

// HTTP Basic Auth gate for the whole dashboard. The password lives in the
// DASHBOARD_PASSWORD env var (set in Vercel); DASHBOARD_USER defaults to "admin".
// With no password configured it fails closed in production so a missing env
// var can never leave the dashboard public.

function unauthorized() {
  return new NextResponse("Authentication required", {
    status: 401,
    headers: { "WWW-Authenticate": 'Basic realm="Keyword Funnel", charset="UTF-8"' },
  });
}

function safeEqual(a: string, b: string) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

export function middleware(req: NextRequest) {
  const password = process.env.DASHBOARD_PASSWORD;
  if (!password) {
    if (process.env.NODE_ENV === "production") {
      return new NextResponse("DASHBOARD_PASSWORD is not configured", { status: 503 });
    }
    return NextResponse.next();
  }

  const user = process.env.DASHBOARD_USER ?? "admin";
  const header = req.headers.get("authorization") ?? "";
  const [scheme, encoded] = header.split(" ");
  if (scheme === "Basic" && encoded) {
    try {
      const decoded = atob(encoded);
      const sep = decoded.indexOf(":");
      if (sep !== -1 && safeEqual(decoded.slice(0, sep), user) && safeEqual(decoded.slice(sep + 1), password)) {
        return NextResponse.next();
      }
    } catch {
      // malformed header falls through to 401
    }
  }
  return unauthorized();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
