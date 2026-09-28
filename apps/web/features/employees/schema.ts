/**
 * Form schemas for the employees feature (react-hook-form + zod).
 * The server stays the authority — these mirror the pinned API contract
 * (`.ai/features/employees.md`): profile PATCH fields only on the employee
 * form; assignment changes go through transfers.
 */

import { z } from "zod";

/** Edit (PATCH) touches profile fields only — pinned PATCH scope. */
export const employeeProfileSchema = z.object({
  first_name: z
    .string()
    .min(1, "First name is required")
    .max(200, "First name must be at most 200 characters"),
  last_name: z
    .string()
    .min(1, "Last name is required")
    .max(200, "Last name must be at most 200 characters"),
  email: z.string().min(1, "Email is required").email("Enter a valid email"),
  phone: z.string().max(50, "Phone must be at most 50 characters").nullish(),
});

export type EmployeeProfileValues = z.infer<typeof employeeProfileSchema>;

/** Create pins the full `CreateEmployee` shape (assignment + hire data). */
export const employeeCreateSchema = employeeProfileSchema.extend({
  department_id: z.string().uuid("Select a department"),
  position_id: z.string().uuid("Select a position"),
  location_id: z.string().uuid("Select a location"),
  manager_id: z.string().uuid("Select a valid manager").nullish(),
  employment_type: z.enum(["full_time", "part_time", "contract", "intern"]),
  hired_at: z.string().min(1, "Hire date is required"),
});

export type EmployeeCreateValues = z.infer<typeof employeeCreateSchema>;

/**
 * Transfer form — position/location/manager hold the sentinel values
 * `"keep"` (inherit the current period) and `"none"` (clear the manager)
 * until submit converts them to omitted fields / explicit null. The API
 * rejects explicit null for position/location, so "keep" must omit the key.
 */
export const transferFormSchema = z.object({
  department_id: z.string().uuid("Select a department"),
  position_id: z.string(),
  location_id: z.string(),
  manager_id: z.string(),
  effective_from: z.string().min(1, "Effective date is required"),
  reason: z.string().max(500, "Reason must be at most 500 characters").nullish(),
});

/** "Keep the current assignment" sentinel (field is omitted on submit). */
export const TRANSFER_KEEP = "keep";
/** "No manager" sentinel — submitted as an explicit null manager. */
export const TRANSFER_NO_MANAGER = "none";

export type TransferFormValues = z.infer<typeof transferFormSchema>;

export const terminationFormSchema = z.object({
  effective_date: z.string().min(1, "Effective date is required"),
  reason: z
    .string()
    .min(1, "A reason is required")
    .max(500, "Reason must be at most 500 characters"),
});

export type TerminationFormValues = z.infer<typeof terminationFormSchema>;
