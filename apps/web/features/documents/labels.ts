/**
 * Stable-code mapping for the documents feature. Human-facing labels live in
 * the `documents` message namespace; wire values never reach the DOM
 * (workpulse-ui §6).
 */

import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";

export type DocumentsErrorKey =
  | "typeNotAllowed"
  | "tooLarge"
  | "uploadNotCompleted"
  | "documentNotFound"
  | "employeeNotFound"
  | "forbidden"
  | "validation"
  | "notFound"
  | "generic";

/** A stable, user-facing sentence for a documents failure (never a raw code). */
export function documentsErrorMessage(
  error: unknown,
  t: (key: DocumentsErrorKey) => string,
): string {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "document_type_not_allowed":
        return t("typeNotAllowed");
      case "document_too_large":
        return t("tooLarge");
      case "document_upload_not_completed":
        return t("uploadNotCompleted");
      case "document_not_found":
        return t("documentNotFound");
      case "employee_not_found":
        return t("employeeNotFound");
      case "forbidden":
        return t("forbidden");
      case "validation_error":
        return t("validation");
      default:
        if (error.status === 403) return t("forbidden");
        if (error.status === 404) return t("notFound");
        return error.message;
    }
  }
  return t("generic");
}

/** The shared role/scope denial classification for read and write surfaces. */
export function isForbiddenError(error: unknown): boolean {
  return error instanceof ApiError && error.status === 403;
}
