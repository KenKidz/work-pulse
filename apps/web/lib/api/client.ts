/**
 * Typed fetch wrapper for the WorkPulse API.
 *
 * Every HTTP call in the app goes through here (directly or via a feature's
 * `api.ts`), so base URL, headers, and error shapes stay consistent.
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

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
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

  return (await response.json()) as T;
}