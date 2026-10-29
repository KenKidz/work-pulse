/**
 * Form schemas and upload policy constants for the documents feature.
 *
 * The server remains the authority: the same allowlist and 20 MB cap are
 * re-checked by the API at intent and confirm time. These mirrors only keep a
 * doomed upload from leaving the browser and give the form translated copy.
 */

import { z } from "zod";

import type { DocumentCategory } from "./types";
import { isCalendarDate } from "./dates";

/** The upload cap pinned by the feature spec (20 MB). */
export const MAX_DOCUMENT_SIZE_BYTES = 20 * 1024 * 1024;

/** MIME types an upload may claim (ADR 0022). */
export const DOCUMENT_CONTENT_TYPES = ["application/pdf", "image/png", "image/jpeg"] as const;

export type DocumentContentType = (typeof DOCUMENT_CONTENT_TYPES)[number];

export function isAllowedContentType(value: string): value is DocumentContentType {
  return (DOCUMENT_CONTENT_TYPES as readonly string[]).includes(value);
}

/** The pinned category set, in menu order. */
export const DOCUMENT_CATEGORIES: DocumentCategory[] = [
  "contract",
  "identification",
  "certification",
  "other",
];

export function isDocumentCategory(value: string): value is DocumentCategory {
  return (DOCUMENT_CATEGORIES as readonly string[]).includes(value);
}

export type DocumentValidationKey =
  | "titleRequired"
  | "titleTooLong"
  | "categoryRequired"
  | "expiryInvalid"
  | "fileRequired"
  | "fileType"
  | "fileTooLarge";

type DocumentValidation = (key: DocumentValidationKey) => string;

/**
 * Upload and edit share the metadata shape. `category` stays a string in the
 * form (Radix Select) and is narrowed once on submit; expiry is optional and
 * may be in the past (historical records are allowed).
 */
export function documentMetadataSchema(t: DocumentValidation) {
  return z.object({
    title: z.string().trim().min(1, t("titleRequired")).max(200, t("titleTooLong")),
    category: z.string().trim().refine(isDocumentCategory, t("categoryRequired")),
    expiry_date: z
      .string()
      .trim()
      .refine((value) => value === "" || isCalendarDate(value), t("expiryInvalid")),
  });
}

// `z.input` keeps the form's own value types: the category is a Select string
// until the schema narrows it, and an untouched file is `null`.
export type DocumentMetadataValues = z.input<ReturnType<typeof documentMetadataSchema>>;

/**
 * The upload form adds the file itself. The browser check mirrors the intent
 * call; the API re-checks the same allowlist and cap at both steps.
 */
export function documentUploadSchema(t: DocumentValidation) {
  return documentMetadataSchema(t).extend({
    file: z
      .custom<File | null>((value) => value instanceof File, t("fileRequired"))
      .superRefine((file, ctx) => {
        if (!(file instanceof File)) return;
        if (!isAllowedContentType(file.type)) {
          ctx.addIssue({ code: z.ZodIssueCode.custom, message: t("fileType") });
          return;
        }
        if (file.size < 1 || file.size > MAX_DOCUMENT_SIZE_BYTES) {
          ctx.addIssue({ code: z.ZodIssueCode.custom, message: t("fileTooLarge") });
        }
      }),
  });
}

export type DocumentUploadValues = z.input<ReturnType<typeof documentUploadSchema>>;
