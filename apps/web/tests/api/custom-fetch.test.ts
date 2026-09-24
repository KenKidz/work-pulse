import { afterEach, describe, expect, it, vi } from "vitest";

import { getHealthzHealthzGetUrl } from "@workpulse/api-client";
import { ApiError, customFetch } from "@workpulse/api-client/mutator/custom-fetch";

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
