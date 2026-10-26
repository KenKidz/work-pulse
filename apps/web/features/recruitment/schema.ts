/**
 * Form schemas for the recruitment workspace (react-hook-form + zod). The
 * server remains the authority — these mirror the pinned contract in
 * `.ai/features/recruitment.md`: title 1..200, description up to 5000,
 * organization references are uuids, and a job has at least one opening.
 */

import { z } from "zod";

const organizationRef = (label: string) => z.string().uuid(`Choose a ${label}`);

export const jobFormSchema = z.object({
  title: z
    .string()
    .trim()
    .min(1, "Enter the job title")
    .max(200, "Title must be at most 200 characters"),
  description: z.string().max(5000, "Description must be at most 5,000 characters"),
  department_id: organizationRef("department"),
  position_id: organizationRef("position"),
  location_id: organizationRef("location"),
  openings: z.coerce
    .number({ invalid_type_error: "Enter the number of openings" })
    .int("Openings must be a whole number")
    .min(1, "A job needs at least one opening"),
});

export type JobFormValues = z.infer<typeof jobFormSchema>;
