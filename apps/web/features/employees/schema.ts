/**
 * Form schemas for the employees feature (react-hook-form + zod).
 * The server stays the authority — these mirror the pinned API contract
 * (`.ai/features/employees.md`): profile PATCH fields only on the employee
 * form; assignment changes go through transfers.
 */

import { z } from "zod";

/** Edit (PATCH) touches profile fields only — pinned PATCH scope. */
type EmployeeValidation = (
  key:
    | "firstNameRequired"
    | "firstNameTooLong"
    | "lastNameRequired"
    | "lastNameTooLong"
    | "emailRequired"
    | "emailInvalid"
    | "phoneTooLong"
    | "departmentRequired"
    | "positionRequired"
    | "locationRequired"
    | "managerInvalid"
    | "hireDateRequired",
) => string;

export function employeeProfileSchema(t: EmployeeValidation) {
  return z.object({
    first_name: z.string().min(1, t("firstNameRequired")).max(200, t("firstNameTooLong")),
    last_name: z.string().min(1, t("lastNameRequired")).max(200, t("lastNameTooLong")),
    email: z.string().min(1, t("emailRequired")).email(t("emailInvalid")),
    phone: z.string().max(50, t("phoneTooLong")).nullish(),
  });
}

export type EmployeeProfileValues = z.infer<ReturnType<typeof employeeProfileSchema>>;

/** Create pins the full `CreateEmployee` shape (assignment + hire data). */
export function employeeCreateSchema(t: EmployeeValidation) {
  return employeeProfileSchema(t).extend({
    department_id: z.string().uuid(t("departmentRequired")),
    position_id: z.string().uuid(t("positionRequired")),
    location_id: z.string().uuid(t("locationRequired")),
    manager_id: z.string().uuid(t("managerInvalid")).nullish(),
    employment_type: z.enum(["full_time", "part_time", "contract", "intern"]),
    hired_at: z.string().min(1, t("hireDateRequired")),
  });
}

export type EmployeeCreateValues = z.infer<ReturnType<typeof employeeCreateSchema>>;

/**
 * Transfer form — position/location/manager hold the sentinel values
 * `"keep"` (inherit the current period) and `"none"` (clear the manager)
 * until submit converts them to omitted fields / explicit null. The API
 * rejects explicit null for position/location, so "keep" must omit the key.
 */
type TransferValidation = (
  key: "departmentRequired" | "effectiveRequired" | "reasonTooLong",
) => string;

export function transferFormSchema(t: TransferValidation) {
  return z.object({
    department_id: z.string().uuid(t("departmentRequired")),
    position_id: z.string(),
    location_id: z.string(),
    manager_id: z.string(),
    effective_from: z.string().min(1, t("effectiveRequired")),
    reason: z.string().max(500, t("reasonTooLong")).nullish(),
  });
}

/** "Keep the current assignment" sentinel (field is omitted on submit). */
export const TRANSFER_KEEP = "keep";
/** "No manager" sentinel — submitted as an explicit null manager. */
export const TRANSFER_NO_MANAGER = "none";

export type TransferFormValues = z.infer<ReturnType<typeof transferFormSchema>>;

type TerminationValidation = (
  key: "effectiveRequired" | "reasonRequired" | "reasonTooLong",
) => string;

export function terminationFormSchema(t: TerminationValidation) {
  return z.object({
    effective_date: z.string().min(1, t("effectiveRequired")),
    reason: z.string().min(1, t("reasonRequired")).max(500, t("reasonTooLong")),
  });
}

export type TerminationFormValues = z.infer<ReturnType<typeof terminationFormSchema>>;
