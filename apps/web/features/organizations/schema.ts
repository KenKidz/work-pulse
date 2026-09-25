/**
 * Form schemas for the organizations feature (react-hook-form + zod).
 * The server stays the authority — these mirror the pinned API contract.
 */

import { z } from "zod";

export const departmentFormSchema = z.object({
  name: z.string().min(1, "Name is required").max(200, "Name must be at most 200 characters"),
  // Explicit null moves the department to the root (pinned PATCH semantics).
  parent_department_id: z.string().uuid("Select a valid parent").nullish(),
});

export type DepartmentFormValues = z.infer<typeof departmentFormSchema>;

export const positionFormSchema = z.object({
  title: z.string().min(1, "Title is required").max(200, "Title must be at most 200 characters"),
  description: z.string().max(2000, "Description must be at most 2000 characters").nullish(),
});

export type PositionFormValues = z.infer<typeof positionFormSchema>;

export const locationFormSchema = z.object({
  name: z.string().min(1, "Name is required").max(200, "Name must be at most 200 characters"),
  address: z.string().max(500, "Address must be at most 500 characters").nullish(),
  timezone: z
    .string()
    .min(1, "Timezone is required")
    .max(100, "Timezone must be at most 100 characters"),
});

export type LocationFormValues = z.infer<typeof locationFormSchema>;
