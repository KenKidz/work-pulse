import { afterEach, describe, expect, it, vi } from "vitest";

import { getHealthzHealthzGetUrl } from "@workpulse/api-client";
import { ApiError, customFetch } from "@workpulse/api-client/mutator/custom-fetch";

const fetchMock = vi.fn();
vi.stubGlobal("fetch", fetchMock);

afterEach(() => {
  fetchMock.mockReset();
});

describe("customFetch", () => {
  it("wraps a 200 response as { data, status, headers } and targets the API base URL", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ status: "ok" }), { status: 200 }),
    );

    const result = await customFetch<{
      data: { status: string };
      status: number;
      headers: Headers;
    }>("/healthz");

    expect(result.data).toEqual({ status: "ok" });
    expect(result.status).toBe(200);
    expect(result.headers).toBeInstanceOf(Headers);
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/healthz",
      expect.objectContaining({
        headers: expect.objectContaining({ "Content-Type": "application/json" }),
      }),
    );
    expect(getHealthzHealthzGetUrl()).toBe("/healthz");
  });

  it("maps an error response to ApiError with the stable error code", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          error: { code: "employee_email_taken", message: "Email already in use" },
        }),
        { status: 409 },
      ),
    );

    const error: unknown = await customFetch("/api/v1/employees").catch((e) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(409);
    expect((error as ApiError).code).toBe("employee_email_taken");
  });

  it("keeps a default message when the error body is not JSON", async () => {
    fetchMock.mockResolvedValueOnce(new Response("gateway timeout", { status: 504 }));

    const error: unknown = await customFetch("/healthz").catch((e) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(504);
    expect((error as ApiError).code).toBeNull();
    expect((error as ApiError).message).toBe("Request failed with status 504");
  });
});

describe("session transport (Phase 2 authN — ADR 0004's swap)", () => {
  it("sends the wp_session cookie cross-origin via credentials: include", async () => {
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ items: [] }), { status: 200 }));

    await customFetch("/api/v1/departments");

    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/v1/departments",
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("sends no dev-identity headers (deleted with the swap, not flagged)", async () => {
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ items: [] }), { status: 200 }));

    await customFetch("/api/v1/departments");

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit | undefined;
    expect(init?.headers).toEqual({ "Content-Type": "application/json" });
  });

  it("keeps credentials: include on requests that pass their own init", async () => {
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ id: "u1" }), { status: 200 }));

    await customFetch("/api/v1/auth/logout", {
      method: "POST",
      headers: { "X-Custom": "1" },
    });

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit | undefined;
    expect(init?.credentials).toBe("include");
    expect(init?.headers).toMatchObject({ "X-Custom": "1" });
  });
});
