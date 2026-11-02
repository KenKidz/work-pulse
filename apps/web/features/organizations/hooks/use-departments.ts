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
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { invalidateDepartments } from "../api";
import {
  sharedApiErrorMessage,
  useApiErrorTranslator,
  type SharedApiErrorTranslator,
} from "@/lib/api-errors";
import type { ListDepartmentsParams } from "../types";

export function useDepartments(params?: ListDepartmentsParams) {
  return useListDepartments(params);
}

type ErrorTranslator = (key: "generic") => string;

export function errorMessage(error: unknown, t: ErrorTranslator): string {
  const shared = sharedApiErrorMessage(error, t as unknown as SharedApiErrorTranslator);
  if (shared !== null) return shared;
  return error instanceof ApiError ? error.message : t("generic");
}

export type DepartmentFieldName = "name" | "parent_department_id";
export type DepartmentFieldErrors = Partial<Record<DepartmentFieldName | "root", string>>;

type FieldErrorTranslator = (
  key: "departmentNameTaken" | "departmentCycle" | "departmentNotFound" | "forbidden" | "generic",
) => string;

/** Map a stable API error code to the field it belongs to (AC9). */
export function departmentFieldErrors(
  error: unknown,
  t: FieldErrorTranslator,
): DepartmentFieldErrors {
  const shared = sharedApiErrorMessage(error, t as unknown as SharedApiErrorTranslator);
  if (shared !== null) return { root: shared };
  if (error instanceof ApiError) {
    switch (error.code) {
      case "department_name_taken":
        return { name: t("departmentNameTaken") };
      case "department_cycle":
        return { parent_department_id: t("departmentCycle") };
      case "department_not_found":
        return { parent_department_id: t("departmentNotFound") };
      case "forbidden":
        return { root: t("forbidden") };
      default:
        return { root: error.message };
    }
  }
  return { root: t("generic") };
}

export function useDepartmentCreate() {
  const t = useTranslations("organizations.toasts");
  const errors = useApiErrorTranslator("organizations.errors");
  const queryClient = useQueryClient();
  return useCreateDepartment({
    mutation: {
      onSuccess: async () => {
        await invalidateDepartments(queryClient);
        toast.success(t("departmentCreated"));
      },
      onError: (error) => toast.error(errorMessage(error, errors)),
    },
  });
}

export function useDepartmentUpdate() {
  const t = useTranslations("organizations.toasts");
  const errors = useApiErrorTranslator("organizations.errors");
  const queryClient = useQueryClient();
  return useUpdateDepartment({
    mutation: {
      onSuccess: async () => {
        await invalidateDepartments(queryClient);
        toast.success(t("departmentUpdated"));
      },
      onError: (error) => toast.error(errorMessage(error, errors)),
    },
  });
}

export function useDepartmentArchive() {
  const t = useTranslations("organizations.toasts");
  const errors = useApiErrorTranslator("organizations.errors");
  const queryClient = useQueryClient();
  return useArchiveDepartment({
    mutation: {
      onSuccess: async () => {
        await invalidateDepartments(queryClient);
        toast.success(t("departmentArchived"));
      },
      onError: (error) => toast.error(errorMessage(error, errors)),
    },
  });
}

export function useDepartmentRestore() {
  const t = useTranslations("organizations.toasts");
  const errors = useApiErrorTranslator("organizations.errors");
  const queryClient = useQueryClient();
  return useRestoreDepartment({
    mutation: {
      onSuccess: async () => {
        await invalidateDepartments(queryClient);
        toast.success(t("departmentRestored"));
      },
      onError: (error) => toast.error(errorMessage(error, errors)),
    },
  });
}
