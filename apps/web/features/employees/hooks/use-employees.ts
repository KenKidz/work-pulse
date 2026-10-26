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
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { invalidateEmployeeAggregates, invalidateEmployees } from "../api";
import type { ListEmployeesParams } from "../types";

export function useEmployees(params?: ListEmployeesParams) {
  return useListEmployees(params);
}

export function useEmployee(id: string) {
  return useGetEmployee(id);
}

type ErrorTranslator = (key: "generic") => string;

export function errorMessage(error: unknown, t: ErrorTranslator): string {
  return error instanceof ApiError ? error.message : t("generic");
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

type EmployeeErrorTranslator = (
  key:
    | "emailTaken"
    | "codeTaken"
    | "managerInvalid"
    | "selfManager"
    | "transferTargetArchived"
    | "departmentNotFound"
    | "positionNotFound"
    | "locationNotFound"
    | "transferEffectiveInvalid"
    | "employmentOverlap"
    | "alreadyTerminated"
    | "forbidden"
    | "generic",
) => string;

/** Map a stable API error code to the field it belongs to (AC12). */
export function employeeFieldErrors(error: unknown, t: EmployeeErrorTranslator): EmployeeFieldErrors {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "employee_email_taken":
        return { email: t("emailTaken") };
      case "employee_code_taken":
        return { root: t("codeTaken") };
      case "employee_manager_invalid":
        return { manager_id: t("managerInvalid") };
      case "employee_self_manager":
        return { manager_id: t("selfManager") };
      case "transfer_target_archived":
        return { root: t("transferTargetArchived") };
      case "department_not_found":
        return { department_id: t("departmentNotFound") };
      case "position_not_found":
        return { position_id: t("positionNotFound") };
      case "location_not_found":
        return { location_id: t("locationNotFound") };
      case "employee_transfer_effective_invalid":
        return { effective_from: t("transferEffectiveInvalid") };
      case "employment_period_overlap":
        return { effective_from: t("employmentOverlap") };
      case "employee_already_terminated":
        return { root: t("alreadyTerminated") };
      case "forbidden":
        return { root: t("forbidden") };
      default:
        return { root: error.message };
    }
  }
  return { root: t("generic") };
}

export function useEmployeeCreate() {
  const t = useTranslations("employees.toasts");
  const errors = useTranslations("employees.errors");
  const queryClient = useQueryClient();
  return useCreateEmployee({
    mutation: {
      onSuccess: async (_data, variables) => {
        await invalidateEmployees(queryClient);
        const code = variables.data.employee_code;
        toast.success(
          code ? t("createdWithCode", { code }) : t("created"),
        );
      },
      onError: (error) => toast.error(errorMessage(error, errors)),
    },
  });
}

export function useEmployeeUpdate() {
  const t = useTranslations("employees.toasts");
  const errors = useTranslations("employees.errors");
  const queryClient = useQueryClient();
  return useUpdateEmployee({
    mutation: {
      onSuccess: async (_data, variables) => {
        await invalidateEmployees(queryClient);
        await invalidateEmployeeAggregates(queryClient, variables.id);
        toast.success(t("updated"));
      },
      onError: (error) => toast.error(errorMessage(error, errors)),
    },
  });
}

export function useEmployeeTransfer() {
  const t = useTranslations("employees.toasts");
  const errors = useTranslations("employees.errors");
  const queryClient = useQueryClient();
  return useTransferEmployee({
    mutation: {
      onSuccess: async (_data, variables) => {
        await invalidateEmployeeAggregates(queryClient, variables.id);
        toast.success(t("transferRecorded"));
      },
      onError: (error) => toast.error(errorMessage(error, errors)),
    },
  });
}

export function useEmployeeTerminate() {
  const t = useTranslations("employees.toasts");
  const errors = useTranslations("employees.errors");
  const queryClient = useQueryClient();
  return useTerminateEmployee({
    mutation: {
      onSuccess: async (_data, variables) => {
        await invalidateEmployeeAggregates(queryClient, variables.id);
        toast.success(t("terminated"));
      },
      onError: (error) => toast.error(errorMessage(error, errors)),
    },
  });
}
