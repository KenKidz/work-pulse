/**
 * Department data hooks — thin wrappers over the generated client hooks.
 *
 * Queries pass parameters through; mutations invalidate the URL-keyed list
 * cache and toast the outcome. `ApiError.code` maps back to form field errors
 * so the server stays the authority (`.ai/conventions.md`).
 */

import { useQueryClient } from "@tanstack/react-query";
import {
  useArchiveDepartment,
  useCreateDepartment,
  useListDepartments,
  useRestoreDepartment,
  useUpdateDepartment,
} from "@workpulse/api-client";
import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";
import { toast } from "sonner";

import { invalidateDepartments } from "../api";
import type { ListDepartmentsParams } from "../types";

export function useDepartments(params?: ListDepartmentsParams) {
  return useListDepartments(params);
}

export function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "Something went wrong. Please try again.";
}

export type DepartmentFieldName = "name" | "parent_department_id";
export type DepartmentFieldErrors = Partial<Record<DepartmentFieldName | "root", string>>;

/** Map a stable API error code to the field it belongs to (AC9). */
export function departmentFieldErrors(error: unknown): DepartmentFieldErrors {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "department_name_taken":
        return { name: "This name is already used by another department." };
      case "department_cycle":
        return { parent_department_id: "This parent would create a cycle in the hierarchy." };
      case "department_not_found":
        return { parent_department_id: "The selected parent department no longer exists." };
      case "forbidden":
        return { root: "You do not have permission to manage organization data." };
      default:
        return { root: error.message };
    }
  }
  return { root: "Something went wrong. Please try again." };
}

export function useDepartmentCreate() {
  const queryClient = useQueryClient();
  return useCreateDepartment({
    mutation: {
      onSuccess: async () => {
        await invalidateDepartments(queryClient);
        toast.success("Department created");
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });
}

export function useDepartmentUpdate() {
  const queryClient = useQueryClient();
  return useUpdateDepartment({
    mutation: {
      onSuccess: async () => {
        await invalidateDepartments(queryClient);
        toast.success("Department updated");
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });
}

export function useDepartmentArchive() {
  const queryClient = useQueryClient();
  return useArchiveDepartment({
    mutation: {
      onSuccess: async () => {
        await invalidateDepartments(queryClient);
        toast.success("Department archived");
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });
}

export function useDepartmentRestore() {
  const queryClient = useQueryClient();
  return useRestoreDepartment({
    mutation: {
      onSuccess: async () => {
        await invalidateDepartments(queryClient);
        toast.success("Department restored");
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });
}
