/**
 * Shared API failure copy (Phase 7, ADR 0025).
 *
 * `429 rate_limited` and `503 dependency_unavailable` are cross-surface
 * outcomes, not feature-specific ones: every toast, dialog and error state
 * renders the same pinned sentence in the active locale. Feature helpers
 * consult this mapping before their own code/status fallbacks, so a raw server
 * message can never leak for either code.
 */

import { useTranslations } from "next-intl";

import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";

export type SharedApiErrorKey = "rateLimited" | "dependencyUnavailable";

/** The `common.errors` translators every feature translator is paired with. */
export type SharedApiErrorTranslator = (key: SharedApiErrorKey) => string;

/**
 * The shared copy key for a failure, or `null` when the feature owns it.
 * Pinned codes win; the status fallback only covers a body with no code, so a
 * feature's own 503 (`reports_unavailable`) keeps its specific sentence.
 */
export function sharedApiErrorKey(error: unknown): SharedApiErrorKey | null {
  if (!(error instanceof ApiError)) return null;
  if (error.code === "rate_limited") return "rateLimited";
  if (error.code === "dependency_unavailable") return "dependencyUnavailable";
  if (error.code === null || error.code === undefined || error.code === "") {
    if (error.status === 429) return "rateLimited";
    if (error.status === 503) return "dependencyUnavailable";
  }
  return null;
}

/** Resolve the shared copy, or `null` for a feature-owned failure. */
export function sharedApiErrorMessage(error: unknown, t: SharedApiErrorTranslator): string | null {
  const key = sharedApiErrorKey(error);
  return key === null ? null : t(key);
}

/**
 * A feature's `errors` translator extended with `common.errors`.
 *
 * Hooks and components pass this in place of `useTranslations("<feature>.errors")`
 * so the feature helpers can resolve shared keys without every call site
 * knowing about the pairing. All other keys still come from the feature
 * namespace.
 */
export function useApiErrorTranslator<Key extends string = string>(
  namespace: string,
): (key: Key | SharedApiErrorKey) => string {
  const feature = useTranslations(namespace as never) as unknown as (key: string) => string;
  const shared = useTranslations("common.errors") as SharedApiErrorTranslator;
  return (key: Key | SharedApiErrorKey) =>
    key === "rateLimited" || key === "dependencyUnavailable"
      ? shared(key as SharedApiErrorKey)
      : feature(key);
}
