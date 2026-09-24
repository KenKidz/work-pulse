/**
 * Fetch transport for the orval-generated client — the single HTTP entry point
 * for the web app (ADR 0005).
 *
 * Owns the base URL and the error contract: non-2xx responses become ApiError
 * carrying the API's stable `error.code`, so UI layers can map codes to
 * user-visible messages. Generated code calls `customFetch`; never import
 * fetch directly in app code.
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

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}
