/**
 * initialsFromEmail tests — the avatar mark derives from the email local part
 * (the `User` wire type carries no display name). Pure function, no render.
 */

import { describe, expect, it } from "vitest";

import { initialsFromEmail } from "@/features/auth/labels";

describe("initialsFromEmail", () => {
  it("takes the first letter of a single-word local part", () => {
    expect(initialsFromEmail("admin@workpulse.test")).toBe("A");
  });

  it("takes the first letters of the first two words", () => {
    expect(initialsFromEmail("e2e.user.munsdvh1@workpulse.test")).toBe("EU");
  });

  it("caps at two words", () => {
    expect(initialsFromEmail("a.b.c@x.test")).toBe("AB");
  });

  it("uppercases the mark", () => {
    expect(initialsFromEmail("hr@workpulse.test")).toBe("H");
  });

  it("splits on underscores and dashes", () => {
    expect(initialsFromEmail("jane_doe@x.test")).toBe("JD");
    expect(initialsFromEmail("jane-doe@x.test")).toBe("JD");
  });

  it("treats a local part without @ as the whole string", () => {
    expect(initialsFromEmail("weird")).toBe("W");
  });

  it("ignores leading separators", () => {
    expect(initialsFromEmail(".admin@x.test")).toBe("A");
  });

  it("returns empty for an empty local part", () => {
    expect(initialsFromEmail("@x.test")).toBe("");
  });
});
