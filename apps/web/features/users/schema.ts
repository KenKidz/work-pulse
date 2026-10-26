/**
 * Form schemas for the users feature (react-hook-form + zod). The server
 * stays the authority — these mirror the pinned API contract
 * (`.ai/features/identity-access.md`): password minimum 10 characters, no
 * complexity rules; PATCH carries email/role/employee_id and never `active`.
 * Factories take the translated validation copy.
 */

import { z } from "zod";

import type { Role } from "./types";
import { ALL_ROLES } from "./components/labels";

type UserValidation = (
  key:
    | "emailRequired"
    | "emailInvalid"
    | "passwordMin"
    | "roleRequired"
    | "employeeInvalid",
) => string;

/** Create pins the full `CreateUser` shape (email + password + role + link). */
export function userCreateSchema(t: UserValidation) {
  return z.object({
    email: z.string().min(1, t("emailRequired")).email(t("emailInvalid")),
    password: z.string().min(10, t("passwordMin")),
    // The single contract-tied role list, shared with every role picker and filter.
    role: z.enum(ALL_ROLES as [Role, ...Role[]], { message: t("roleRequired") }),
    employee_id: z.string().uuid(t("employeeInvalid")).nullish(),
  });
}

export type UserCreateValues = z.infer<ReturnType<typeof userCreateSchema>>;

/** Edit (PATCH) touches email/role/employee link only — `active` is excluded. */
export function userEditSchema(t: UserValidation) {
  return z.object({
    email: z.string().min(1, t("emailRequired")).email(t("emailInvalid")),
    role: z.enum(ALL_ROLES as [Role, ...Role[]], { message: t("roleRequired") }),
    employee_id: z.string().uuid(t("employeeInvalid")).nullish(),
  });
}

export type UserEditValues = z.infer<ReturnType<typeof userEditSchema>>;

/** Admin password reset — same minimum as creation (pinned). */
export function resetPasswordSchema(t: (key: "passwordMin") => string) {
  return z.object({
    new_password: z.string().min(10, t("passwordMin")),
  });
}

export type ResetPasswordValues = z.infer<ReturnType<typeof resetPasswordSchema>>;
