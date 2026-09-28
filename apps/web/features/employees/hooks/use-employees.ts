/**
 * Employee data hooks — thin wrappers over the generated client hooks.
 *
 * Queries pass parameters through; mutations invalidate the URL-keyed caches
 * and toast the outcome. `ApiError.code` maps back to form field errors so
 * the server stays the authority (`.ai/conventions.md`).
 */

import { useQueryClient } from "@tanstack/react-query";
import {
  useCreateEmployee,
  useGetEmployee,
  useListEmployees,
  useTerminateEmployee,
  useTransferEmployee,
  useUpdateEmployee,
} from "@workpulse/api-client";
import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";
import { toast } from "sonner";

import { invalidateEmployeeAggregates, invalidateEmployees } from "../api";
import type { ListEmployeesParams } from "../types";

export function useEmployees(params?: ListEmployeesParams) {
  return useListEmployees(params);
}

export function useEmployee(id: string) {
  return useGetEmployee(id);
}

export function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "Something went wrong. Please try again.";
}

export type EmployeeFieldName =
  | "first_name"
  | "last_name"
  | "email"
  | "phone"
  | "department_id"
  | "position_id"
  | "location_id"
  | "manager_id"
  | "hired_at";

export type TransferFieldName =
  "department_id" | "position_id" | "location_id" | "manager_id" | "effective_from" | "reason";

export type EmployeeFieldErrors = Partial<
  Record<EmployeeFieldName | TransferFieldName | "root", string>
>;

/** Map a stable API error code to the field it belongs to (AC12). */
export function employeeFieldErrors(error: unknown): EmployeeFieldErrors {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "employee_email_taken":
        return { email: "An employee with this email already exists." };
      case "employee_code_taken":
        return { root: "This employee code is already used by another employee." };
      case "employee_manager_invalid":
        return { manager_id: "The selected manager must be an active employee." };
      case "employee_self_manager":
        return { manager_id: "An employee cannot be their own manager." };
      case "transfer_target_archived":
        return { root: "The selected department, position or location is archived." };
      case "department_not_found":
        return { department_id: "The selected department no longer exists." };
      case "position_not_found":
        return { position_id: "The selected position no longer exists." };
      case "location_not_found":
        return { location_id: "The selected location no longer exists." };
      case "employee_transfer_effective_invalid":
        return {
          effective_from:
            "The effective date must be within employment — not before the hire date and not in the future.",
        };
      case "employment_period_overlap":
        return { effective_from: "This effective date overlaps an existing employment period." };
      case "employee_already_terminated":
        return { root: "This employee has already been terminated." };
      case "forbidden":
        return { root: "You do not have permission to manage employees." };
      default:
        return { root: error.message };
    }
  }
  return { root: "Something went wrong. Please try again." };
}

export function useEmployeeCreate() {
  const queryClient = useQueryClient();
  return useCreateEmployee({
    mutation: {
      onSuccess: async (_data, variables) => {
        await invalidateEmployees(queryClient);
        const code = variables.data.employee_code;
        toast.success(code ? `Employee ${code} created` : "Employee created");
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });
}

export function useEmployeeUpdate() {
  const queryClient = useQueryClient();
  return useUpdateEmployee({
    mutation: {
      onSuccess: async (_data, variables) => {
        await invalidateEmployees(queryClient);
        await invalidateEmployeeAggregates(queryClient, variables.id);
        toast.success("Employee updated");
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });
}

export function useEmployeeTransfer() {
  const queryClient = useQueryClient();
  return useTransferEmployee({
    mutation: {
      onSuccess: async (_data, variables) => {
        await invalidateEmployeeAggregates(queryClient, variables.id);
        toast.success("Transfer recorded");
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });
}

export function useEmployeeTerminate() {
  const queryClient = useQueryClient();
  return useTerminateEmployee({
    mutation: {
      onSuccess: async (_data, variables) => {
        await invalidateEmployeeAggregates(queryClient, variables.id);
        toast.success("Employee terminated");
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });
}
