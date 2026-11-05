import { NextRequest, NextResponse } from "next/server";

import { getAuthenticatedUser, SESSION_COOKIE } from "./lib/auth";

export async function middleware(request: NextRequest) {
  if (request.nextUrl.pathname === "/login") {
    return NextResponse.next();
  }

  const token = request.cookies.get(SESSION_COOKIE)?.value;
  if (!token) {
    return redirectToLogin(request);
  }

  try {
    const user = await getAuthenticatedUser(token);
    if (!user) {
      const response = redirectToLogin(request);
      response.cookies.delete(SESSION_COOKIE);
      return response;
    }

    if (request.nextUrl.pathname.startsWith("/admin/approvals") && user.role !== "approver") {
      return NextResponse.redirect(new URL("/?denied=approver", request.url));
    }
    return NextResponse.next();
  } catch {
    return NextResponse.redirect(new URL("/login?error=auth-service", request.url));
  }
}

function redirectToLogin(request: NextRequest) {
  const loginUrl = new URL("/login", request.url);
  loginUrl.searchParams.set("returnTo", `${request.nextUrl.pathname}${request.nextUrl.search}`);
  return NextResponse.redirect(loginUrl);
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|api/auth|api/advisor).*)"],
};
