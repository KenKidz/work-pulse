/**
 * Catalog parity — `en` is the source of truth for the message shape and `vi`
 * must mirror it exactly. A missing translation is a gate failure, not a
 * silent English leak.
 */

import { describe, expect, it } from "vitest";

import en from "@/messages/en";
import vi from "@/messages/vi";

type Tree = { [key: string]: string | Tree };

function keyPaths(tree: Tree, prefix = ""): string[] {
  return Object.entries(tree).flatMap(([key, value]) => {
    const path = prefix === "" ? key : `${prefix}.${key}`;
    return typeof value === "string" ? [path] : keyPaths(value, path);
  });
}

function leafEntries(tree: Tree, prefix = ""): Array<[string, string]> {
  return Object.entries(tree).flatMap(([key, value]) => {
    const path = prefix === "" ? key : `${prefix}.${key}`;
    return typeof value === "string" ? [[path, value]] : leafEntries(value, path);
  });
}

describe("message catalogs", () => {
  it("exposes the same key set in every locale", () => {
    const english = keyPaths(en as unknown as Tree).sort();
    const vietnamese = keyPaths(vi as unknown as Tree).sort();
    expect(vietnamese).toEqual(english);
  });

  it("has no empty or whitespace-only message", () => {
    for (const [locale, tree] of [
      ["en", en],
      ["vi", vi],
    ] as const) {
      for (const [key, value] of leafEntries(tree as unknown as Tree)) {
        expect(value.trim(), `${locale}:${key}`).not.toBe("");
      }
    }
  });
});
