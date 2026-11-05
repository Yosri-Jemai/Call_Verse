export const SESSION_COOKIE = "cashy_access_token";

export type RelayUser = {
  id: number;
  username: string;
  role: "approver" | "viewer";
};

function advisorApiUrl(): string {
  return process.env.NEXT_PUBLIC_ADVISOR_API_URL || "http://127.0.0.1:8000";
}

export async function getAuthenticatedUser(token: string): Promise<RelayUser | null> {
  let response: Response;
  try {
    response = await fetch(`${advisorApiUrl()}/auth/me`, {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
      signal: AbortSignal.timeout(5000),
    });
  } catch {
    throw new Error("The Customer Advisor authentication service is unavailable.");
  }

  if (response.status === 401) return null;
  if (!response.ok) {
    throw new Error("The Customer Advisor authentication service is unavailable.");
  }

  const user = (await response.json()) as RelayUser;
  if (
    !Number.isSafeInteger(user.id) ||
    !user.username ||
    (user.role !== "approver" && user.role !== "viewer")
  ) {
    throw new Error("The authentication service returned an invalid user.");
  }
  return user;
}

export function advisorApiBaseUrl(): string {
  return process.env.NEXT_PUBLIC_ADVISOR_API_URL || "http://127.0.0.1:8000";
}
