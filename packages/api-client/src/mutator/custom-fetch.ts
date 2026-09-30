/**
 * Fetch transport for the orval-generated client — the single HTTP entry point
 * for the web app (ADR 0005).
 *
 * Owns the base URL, the cookie-based session (Phase 2 authN: `credentials:
 * "include"` sends the `wp_session` cookie; the API CORS middleware allows
 * credentials) and the error contract: non-2xx responses become ApiError
 * carrying the API's stable `error.code`, so UI layers can map codes to
 * user-visible messages. Generated code calls `customFetch`; never import
 * fetch directly in app code.
 *
 * Return contract: `{ data, status, headers }` — the shape orval's generated
 * response unions declare and every consumer discriminates on
 * (`data.status === 200`). Keep it in sync with `orval.config.ts`.
 */

const DEFAULT_BASE_URL = "http://localhost:8000";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string | null;

  constructor(status: number, code: string | null, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

export function apiBaseUrl(): string {
  return process.env.NEXT_PUBLIC_API_URL ?? DEFAULT_BASE_URL;
}

export async function customFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBaseUrl()}${path}`, {
    ...init,
    // The session cookie is the identity (ADR 0004's swap): send it on every
    // request, including cross-origin localhost:3000 → localhost:8000.
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });

  if (!response.ok) {
    let code: string | null = null;
    let message = `Request failed with status ${response.status}`;

    try {
      const body = (await response.json()) as {
        error?: { code?: string; message?: string };
      };
      code = body.error?.code ?? null;
      message = body.error?.message ?? message;
    } catch {
      // Non-JSON error body — keep the defaults.
    }

    throw new ApiError(response.status, code, message);
  }

  const data = response.status === 204 ? undefined : await response.json();

  return { data, status: response.status, headers: response.headers } as T;
}
