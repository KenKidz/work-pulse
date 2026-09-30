/**
 * Form schemas for the users feature (react-hook-form + zod). The server
 * stays the authority — these mirror the pinned API contract
 * (`.ai/features/identity-access.md`): password minimum 10 characters, no
 * complexity rules; PATCH carries email/role/employee_id and never `active`.
 */

import { z } from "zod";

import type { Role } from "./types";
import { ALL_ROLES } from "./components/labels";

/** Create pins the full `CreateUser` shape (email + password + role + link). */
export const userCreateSchema = z.object({
  email: z.string().min(1, "Email is required").email("Enter a valid email"),
  password: z.string().min(10, "Password must be at least 10 characters"),
  // The single contract-tied role list (derived from `roleLabel`'s
  // `Record<Role, string>`), shared with every role picker and filter.
  role: z.enum(ALL_ROLES as [Role, ...Role[]], { message: "Select a role" }),
  employee_id: z.string().uuid("Select a valid employee").nullish(),
});

export type UserCreateValues = z.infer<typeof userCreateSchema>;

/** Edit (PATCH) touches email/role/employee link only — `active` is excluded. */
export const userEditSchema = z.object({
  email: z.string().min(1, "Email is required").email("Enter a valid email"),
  role: z.enum(ALL_ROLES as [Role, ...Role[]], { message: "Select a role" }),
  employee_id: z.string().uuid("Select a valid employee").nullish(),
});

export type UserEditValues = z.infer<typeof userEditSchema>;

/** Admin password reset — same minimum as creation (pinned). */
export const resetPasswordSchema = z.object({
  new_password: z.string().min(10, "Password must be at least 10 characters"),
});

export type ResetPasswordValues = z.infer<typeof resetPasswordSchema>;
