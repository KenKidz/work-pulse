/**
 * Form schemas for the organizations feature (react-hook-form + zod).
 * The server stays the authority — these mirror the pinned API contract.
 * Factories take the translated validation copy so the schemas stay
 * locale-agnostic.
 */

import { z } from "zod";

type DepartmentValidation = (
  key: "nameRequired" | "nameTooLong" | "parentInvalid",
) => string;

export function departmentFormSchema(t: DepartmentValidation) {
  return z.object({
    name: z.string().min(1, t("nameRequired")).max(200, t("nameTooLong")),
    // Explicit null moves the department to the root (pinned PATCH semantics).
    parent_department_id: z.string().uuid(t("parentInvalid")).nullish(),
  });
}

export type DepartmentFormValues = z.infer<ReturnType<typeof departmentFormSchema>>;

type PositionValidation = (
  key: "titleRequired" | "titleTooLong" | "descriptionTooLong",
) => string;

export function positionFormSchema(t: PositionValidation) {
  return z.object({
    title: z.string().min(1, t("titleRequired")).max(200, t("titleTooLong")),
    description: z.string().max(2000, t("descriptionTooLong")).nullish(),
  });
}

export type PositionFormValues = z.infer<ReturnType<typeof positionFormSchema>>;

type LocationValidation = (
  key:
    | "nameRequired"
    | "nameTooLong"
    | "addressTooLong"
    | "timezoneRequired"
    | "timezoneTooLong",
) => string;

export function locationFormSchema(t: LocationValidation) {
  return z.object({
    name: z.string().min(1, t("nameRequired")).max(200, t("nameTooLong")),
    address: z.string().max(500, t("addressTooLong")).nullish(),
    timezone: z
      .string()
      .min(1, t("timezoneRequired"))
      .max(100, t("timezoneTooLong")),
  });
}

export type LocationFormValues = z.infer<ReturnType<typeof locationFormSchema>>;
