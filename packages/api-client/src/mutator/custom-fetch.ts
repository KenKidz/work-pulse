/**
 * Fetch transport for the orval-generated client — the single HTTP entry point
 * for the web app (ADR 0005).
 *
 * Owns the base URL, the Phase 1 dev-identity headers (ADR 0004 — a temporary
 * trust boundary, removed with Phase 2 authN) and the error contract: non-2xx
 * responses become ApiError carrying the API's stable `error.code`, so UI
 * layers can map codes to user-visible messages. Generated code calls
 * `customFetch`; never import fetch directly in app code.
 *
 * Return contract: `{ data, status, headers }` — the shape orval's generated
 * response unions declare and every consumer discriminates on
 * (`data.status === 200`). Keep it in sync with `orval.config.ts`.
 */

const DEFAULT_BASE_URL = "http://localhost:8000";

// Dev-identity defaults match database/postgres/seeds (dev tenant + actor).
const DEFAULT_DEV_TENANT_ID = "00000000-0000-4000-8000-000000000001";
const DEFAULT_DEV_ACTOR_ID = "00000000-0000-4000-8000-000000000002";
const DEFAULT_DEV_ROLE = "admin";

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

/**
 * Phase 1 identity is the dev identity: three headers injected by the
 * transport, overridable per environment (CI/e2e can switch roles). Read at
 * call time so tests and runtime overrides win.
 */
export function devIdentityHeaders(): Record<string, string> {
  return {
    "X-Tenant-Id": process.env.NEXT_PUBLIC_DEV_TENANT_ID ?? DEFAULT_DEV_TENANT_ID,
    "X-Actor-Id": process.env.NEXT_PUBLIC_DEV_ACTOR_ID ?? DEFAULT_DEV_ACTOR_ID,
    "X-Role": process.env.NEXT_PUBLIC_DEV_ROLE ?? DEFAULT_DEV_ROLE,
  };
}

export async function customFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBaseUrl()}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...devIdentityHeaders(),
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
