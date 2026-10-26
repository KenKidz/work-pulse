/**
 * Locale server action — only known locales reach the cookie; a hand-crafted
 * invalid value must leave the current preference untouched.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

const cookieSet = vi.hoisted(() => vi.fn());

vi.mock("next/headers", () => ({
  cookies: async () => ({ set: cookieSet }),
}));

import { setLocale } from "@/i18n/actions";
import { LOCALE_COOKIE } from "@/i18n/config";

describe("setLocale", () => {
  beforeEach(() => {
    cookieSet.mockReset();
  });

  it("persists a supported locale", async () => {
    await setLocale("vi");
    expect(cookieSet).toHaveBeenCalledWith(
      LOCALE_COOKIE,
      "vi",
      expect.objectContaining({ path: "/", sameSite: "lax" }),
    );
  });

  it("ignores an unsupported locale", async () => {
    await setLocale("fr");
    expect(cookieSet).not.toHaveBeenCalled();
  });
});
