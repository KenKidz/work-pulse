"use client";

/**
 * Performance data hooks — thin wrappers over the generated client hooks.
 *
 * The API is the authority for scope (own / self + direct reports / tenant):
 * the access hook only decides which controls render. Goal mutations
 * invalidate the URL-keyed goal lists so a transition lands without a reload;
 * `ApiError.code` maps back to the dialog that caused it.
 */

import { useQueryClient } from "@tanstack/react-query";
import {
  useActivateGoal,
  useCancelGoal,
  useCompleteGoal,
  useCreateGoal,
  useGetCurrentEmployee,
  useListEmployees,
  useListGoals,
  useListReviews,
  useListSkills,
  useUpdateGoal,
} from "@workpulse/api-client";
import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { useAuth } from "@/features/auth/components/AuthGate";

import { invalidateGoals } from "../api";
import { performanceErrorMessage, type PerformanceErrorKey } from "../labels";
import {
  EMPLOYEE_SKILL_ROLES,
  GOAL_MANAGE_ROLES,
  PERFORMANCE_TEAM_ROLES,
  PERFORMANCE_TENANT_ROLES,
  REVIEW_AUTHOR_ROLES,
  SKILL_CATALOG_ROLES,
} from "../roles";
import type {
  Employee,
  EmployeeSummary,
  ListGoalsParams,
  ListReviewsParams,
  ListSkillsParams,
} from "../types";

/** The web-side performance gate and capability matrix. */
export function usePerformanceAccess() {
  const { user } = useAuth();
  const canViewTeam = PERFORMANCE_TEAM_ROLES.includes(user.role);
  const canManageGoals = GOAL_MANAGE_ROLES.includes(user.role);
  const canRunReviews = REVIEW_AUTHOR_ROLES.includes(user.role);
  const canManageCatalog = SKILL_CATALOG_ROLES.includes(user.role);
  const canRecordSkills = EMPLOYEE_SKILL_ROLES.includes(user.role);
  // Employee/Finance (and a manager without an employee link) have no own
  // record to show; HR/Admin/Super Admin still have the tenant scope.
  const canUseWorkspace = user.employee_id != null || PERFORMANCE_TENANT_ROLES.includes(user.role);

  return {
    user,
    canViewTeam,
    canManageGoals,
    canRunReviews,
    canManageCatalog,
    canRecordSkills,
    canUseWorkspace,
  };
}

const DIRECTORY_PAGE_SIZE = 100;

/**
 * Employee names and active picker options. The directory is readable by
 * Manager/HR/Admin (the API narrows a manager to direct reports); Employee and
 * Finance resolve only their own link. Query hooks are gated so a role that
 * cannot read the directory fires no refused request.
 */
export function usePerformanceDirectory() {
  const { user } = useAuth();
  const canReadEmployees = PERFORMANCE_TEAM_ROLES.includes(user.role);
  const employeesQuery = useListEmployees(
    { page_size: DIRECTORY_PAGE_SIZE },
    { query: { enabled: canReadEmployees } },
  );
  const meQuery = useGetCurrentEmployee({
    query: { enabled: canReadEmployees && user.employee_id != null },
  });

  const employees: EmployeeSummary[] =
    employeesQuery.data?.status === 200 ? employeesQuery.data.data.items : [];
  const me: Employee | null = meQuery.data?.status === 200 ? meQuery.data.data : null;

  const nameById = new Map(
    employees.map((employee) => [
      employee.id,
      `${employee.first_name} ${employee.last_name}`.trim(),
    ]),
  );
  if (me !== null) {
    nameById.set(me.id, `${me.first_name} ${me.last_name}`.trim());
  }

  const activeEmployees = employees.filter((employee) => employee.status === "active");
  // A manager's directory may contain only direct reports; self is always a
  // valid subject/goal owner, so append the resolved link when it is missing.
  const pickerEmployees =
    me !== null && me.status === "active" && !activeEmployees.some((row) => row.id === me.id)
      ? [...activeEmployees, me]
      : activeEmployees;
  const filterEmployees =
    me !== null && !employees.some((row) => row.id === me.id) ? [...employees, me] : employees;

  return {
    canReadEmployees,
    employees,
    filterEmployees,
    pickerEmployees,
    me,
    nameById,
  };
}

export function useGoals(params?: ListGoalsParams) {
  return useListGoals(params);
}

export function useReviews(params?: ListReviewsParams) {
  return useListReviews(params);
}

export function useSkills(params?: ListSkillsParams) {
  return useListSkills(params);
}

export type GoalFieldName = "employee_id" | "root";
export type GoalFieldErrors = Partial<Record<GoalFieldName, string>>;

/**
 * Map a pinned goal failure code to the dialog. The subject picker only
 * renders on create for team roles, so its errors collapse to the dialog root
 * everywhere else (a hidden field must not swallow the sentence).
 */
export function goalFieldErrors(
  error: unknown,
  t: (key: PerformanceErrorKey) => string,
  { hasEmployeeField }: { hasEmployeeField: boolean },
): GoalFieldErrors {
  const message = performanceErrorMessage(error, t);
  if (hasEmployeeField && error instanceof ApiError) {
    switch (error.code) {
      case "employee_not_found":
      case "employee_not_active":
        return { employee_id: message };
      default:
        break;
    }
  }
  return { root: message };
}

export function useGoalCreate() {
  const t = useTranslations("performance.toasts");
  const errors = useTranslations("performance.errors");
  const queryClient = useQueryClient();
  return useCreateGoal({
    mutation: {
      onSuccess: async () => {
        await invalidateGoals(queryClient);
        toast.success(t("goalCreated"));
      },
      onError: (error) => toast.error(performanceErrorMessage(error, errors)),
    },
  });
}

export function useGoalUpdate() {
  const t = useTranslations("performance.toasts");
  const errors = useTranslations("performance.errors");
  const queryClient = useQueryClient();
  return useUpdateGoal({
    mutation: {
      onSuccess: async () => {
        await invalidateGoals(queryClient);
        toast.success(t("goalUpdated"));
      },
      onError: (error) => toast.error(performanceErrorMessage(error, errors)),
    },
  });
}

/** `draft → active`; a stale row surfaces `goal_not_draft` as a toast. */
export function useGoalActivate() {
  const t = useTranslations("performance.toasts");
  const errors = useTranslations("performance.errors");
  const queryClient = useQueryClient();
  return useActivateGoal({
    mutation: {
      onSuccess: async () => {
        await invalidateGoals(queryClient);
        toast.success(t("goalActivated"));
      },
      onError: (error) => toast.error(performanceErrorMessage(error, errors)),
    },
  });
}

export function useGoalComplete() {
  const t = useTranslations("performance.toasts");
  const errors = useTranslations("performance.errors");
  const queryClient = useQueryClient();
  return useCompleteGoal({
    mutation: {
      onSuccess: async () => {
        await invalidateGoals(queryClient);
        toast.success(t("goalCompleted"));
      },
      onError: (error) => toast.error(performanceErrorMessage(error, errors)),
    },
  });
}

export function useGoalCancel() {
  const t = useTranslations("performance.toasts");
  const errors = useTranslations("performance.errors");
  const queryClient = useQueryClient();
  return useCancelGoal({
    mutation: {
      onSuccess: async () => {
        await invalidateGoals(queryClient);
        toast.success(t("goalCancelled"));
      },
      onError: (error) => toast.error(performanceErrorMessage(error, errors)),
    },
  });
}
