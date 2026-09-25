import { afterEach, describe, expect, it, vi } from "vitest";

import { getHealthzHealthzGetUrl } from "@workpulse/api-client";
import {
  ApiError,
  customFetch,
  devIdentityHeaders,
} from "@workpulse/api-client/mutator/custom-fetch";

const fetchMock = vi.fn();
vi.stubGlobal("fetch", fetchMock);

afterEach(() => {
  fetchMock.mockReset();
});

describe("customFetch", () => {
  it("returns parsed JSON for a 200 response and targets the API base URL", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ status: "ok" }), { status: 200 }),
    );

    await expect(customFetch<{ status: string }>("/healthz")).resolves.toEqual({
      status: "ok",
    });
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

describe("dev identity headers (ADR 0004 — temporary trust boundary)", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("defaults to the seeded dev tenant/actor and the admin role", () => {
    expect(devIdentityHeaders()).toEqual({
      "X-Tenant-Id": "00000000-0000-4000-8000-000000000001",
      "X-Actor-Id": "00000000-0000-4000-8000-000000000002",
      "X-Role": "admin",
    });
  });

  it("sends the dev identity headers on every request", async () => {
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ items: [] }), { status: 200 }));

    await customFetch("/api/v1/departments");

    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/v1/departments",
      expect.objectContaining({
        headers: expect.objectContaining({
          "X-Tenant-Id": "00000000-0000-4000-8000-000000000001",
          "X-Actor-Id": "00000000-0000-4000-8000-000000000002",
          "X-Role": "admin",
        }),
      }),
    );
  });

  it("prefers the NEXT_PUBLIC_DEV_* environment overrides", async () => {
    vi.stubEnv("NEXT_PUBLIC_DEV_TENANT_ID", "00000000-0000-4000-8000-0000000000ff");
    vi.stubEnv("NEXT_PUBLIC_DEV_ACTOR_ID", "00000000-0000-4000-8000-0000000000ee");
    vi.stubEnv("NEXT_PUBLIC_DEV_ROLE", "hr");

    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ items: [] }), { status: 200 }));
    await customFetch("/api/v1/departments");

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit | undefined;
    expect(init?.headers).toMatchObject({
      "X-Tenant-Id": "00000000-0000-4000-8000-0000000000ff",
      "X-Actor-Id": "00000000-0000-4000-8000-0000000000ee",
      "X-Role": "hr",
    });
  });
});
