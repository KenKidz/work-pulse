"use client";

/**
 * Performance data hooks — thin wrappers over the generated client hooks.
 *
 * The API is the authority for scope (own / self + direct reports / tenant):
 * the access hook only decides which controls render. Every mutation
 * invalidates its URL-keyed lists so a transition lands without a reload;
 * `ApiError.code` maps back to the dialog that caused it.
 */

import { useQueryClient } from "@tanstack/react-query";
import {
  useActivateGoal,
  useArchiveSkill,
  useCancelGoal,
  useCancelReview,
  useCompleteGoal,
  useCompleteReview,
  useCreateGoal,
  useCreateReview,
  useCreateSkill,
  useGetCurrentEmployee,
  useListEmployeeSkills,
  useListEmployees,
  useListGoals,
  useListReviews,
  useListSkills,
  useRecordEmployeeSkill,
  useRemoveEmployeeSkill,
  useRestoreSkill,
  useSubmitReviewFeedback,
  useUpdateEmployeeSkill,
  useUpdateGoal,
  useUpdateReview,
  useUpdateSkill,
} from "@workpulse/api-client";
import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { useAuth } from "@/features/auth/components/AuthGate";

import { useApiErrorTranslator } from "@/lib/api-errors";

import {
  invalidateAllEmployeeSkills,
  invalidateEmployeeSkills,
  invalidateGoals,
  invalidateReviewFeedback,
  invalidateReviews,
  invalidateSkills,
} from "../api";
import { performanceErrorMessage, type PerformanceErrorKey } from "../labels";
import {
  EMPLOYEE_SKILL_ROLES,
  FEEDBACK_NOMINATE_ROLES,
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
  const canViewFeedback = FEEDBACK_NOMINATE_ROLES.includes(user.role);
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
    canViewFeedback,
    canManageCatalog,
    canRecordSkills,
    canUseWorkspace,
  };
}

const DIRECTORY_PAGE_SIZE = 100;

/**
 * Employee names and active picker options. The directory is readable by
 * Manager/HR/Admin (the API narrows a manager to direct reports); every role
 * can read its own `/employees/me`, so an Employee/Finance surface still
 * resolves the caller's real name. The list hook is gated so a role that
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
    query: { enabled: user.employee_id != null },
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

export function useReviews(
  params?: ListReviewsParams,
  options?: { query?: { enabled?: boolean } },
) {
  return useListReviews(params, options);
}

export function useSkills(params?: ListSkillsParams) {
  return useListSkills(params);
}

/** The attach picker: active catalog rows only (archived never appears). */
export function useActiveSkills() {
  return useListSkills({ page_size: 100 });
}

/** Employee-skill rows for one subject; an empty subject disables the query. */
export function useEmployeeSkills(employeeId: string) {
  return useListEmployeeSkills(employeeId, {
    query: { enabled: employeeId !== "" },
  });
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
  const errors = useApiErrorTranslator("performance.errors");
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
  const errors = useApiErrorTranslator("performance.errors");
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
  const errors = useApiErrorTranslator("performance.errors");
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
  const errors = useApiErrorTranslator("performance.errors");
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
  const errors = useApiErrorTranslator("performance.errors");
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

export type ReviewFieldName = "employee_id" | "feedback_provider_ids" | "root";
export type ReviewFieldErrors = Partial<Record<ReviewFieldName, string>>;

/**
 * Map review-form failures onto the field that caused them. The subject picker
 * exists only on create and the provider group only when nomination is
 * editable; a hidden field collapses to the dialog root.
 */
export function reviewFieldErrors(
  error: unknown,
  t: (key: PerformanceErrorKey) => string,
  { hasEmployeeField, hasProviderField }: { hasEmployeeField: boolean; hasProviderField: boolean },
): ReviewFieldErrors {
  const message = performanceErrorMessage(error, t);
  if (error instanceof ApiError) {
    switch (error.code) {
      case "employee_not_found":
      case "employee_not_active":
        return hasEmployeeField ? { employee_id: message } : { root: message };
      case "feedback_provider_invalid":
      case "feedback_already_submitted":
        return hasProviderField ? { feedback_provider_ids: message } : { root: message };
      default:
        break;
    }
  }
  return { root: message };
}

export function useReviewCreate() {
  const t = useTranslations("performance.toasts");
  const errors = useApiErrorTranslator("performance.errors");
  const queryClient = useQueryClient();
  return useCreateReview({
    mutation: {
      onSuccess: async () => {
        await invalidateReviews(queryClient);
        toast.success(t("reviewCreated"));
      },
      onError: (error) => toast.error(performanceErrorMessage(error, errors)),
    },
  });
}

export function useReviewUpdate() {
  const t = useTranslations("performance.toasts");
  const errors = useApiErrorTranslator("performance.errors");
  const queryClient = useQueryClient();
  return useUpdateReview({
    mutation: {
      onSuccess: async () => {
        await invalidateReviews(queryClient);
        toast.success(t("reviewUpdated"));
      },
      onError: (error) => toast.error(performanceErrorMessage(error, errors)),
    },
  });
}

export function useReviewComplete() {
  const t = useTranslations("performance.toasts");
  const errors = useApiErrorTranslator("performance.errors");
  const queryClient = useQueryClient();
  return useCompleteReview({
    mutation: {
      onSuccess: async () => {
        await invalidateReviews(queryClient);
        toast.success(t("reviewCompleted"));
      },
      onError: (error) => toast.error(performanceErrorMessage(error, errors)),
    },
  });
}

export function useReviewCancel() {
  const t = useTranslations("performance.toasts");
  const errors = useApiErrorTranslator("performance.errors");
  const queryClient = useQueryClient();
  return useCancelReview({
    mutation: {
      onSuccess: async () => {
        await invalidateReviews(queryClient);
        toast.success(t("reviewCancelled"));
      },
      onError: (error) => toast.error(performanceErrorMessage(error, errors)),
    },
  });
}

/** File the nominated provider's one comment; both review lists refresh. */
export function useReviewFeedbackSubmit() {
  const t = useTranslations("performance.toasts");
  const errors = useApiErrorTranslator("performance.errors");
  const queryClient = useQueryClient();
  return useSubmitReviewFeedback({
    mutation: {
      onSuccess: async (_data, variables) => {
        await Promise.all([
          invalidateReviews(queryClient),
          invalidateReviewFeedback(queryClient, variables.id),
        ]);
        toast.success(t("feedbackSubmitted"));
      },
      onError: (error) => toast.error(performanceErrorMessage(error, errors)),
    },
  });
}

export type SkillFieldName = "name" | "root";
export type SkillFieldErrors = Partial<Record<SkillFieldName, string>>;

export function skillFieldErrors(
  error: unknown,
  t: (key: PerformanceErrorKey) => string,
): SkillFieldErrors {
  const message = performanceErrorMessage(error, t);
  if (error instanceof ApiError && error.code === "skill_name_taken") {
    return { name: message };
  }
  return { root: message };
}

export function useSkillCreate() {
  const t = useTranslations("performance.toasts");
  const errors = useApiErrorTranslator("performance.errors");
  const queryClient = useQueryClient();
  return useCreateSkill({
    mutation: {
      onSuccess: async () => {
        await invalidateSkills(queryClient);
        toast.success(t("skillCreated"));
      },
      onError: (error) => toast.error(performanceErrorMessage(error, errors)),
    },
  });
}

export function useSkillUpdate() {
  const t = useTranslations("performance.toasts");
  const errors = useApiErrorTranslator("performance.errors");
  const queryClient = useQueryClient();
  return useUpdateSkill({
    mutation: {
      onSuccess: async () => {
        await Promise.all([
          invalidateSkills(queryClient),
          invalidateAllEmployeeSkills(queryClient),
        ]);
        toast.success(t("skillUpdated"));
      },
      onError: (error) => toast.error(performanceErrorMessage(error, errors)),
    },
  });
}

export function useSkillArchive() {
  const t = useTranslations("performance.toasts");
  const errors = useApiErrorTranslator("performance.errors");
  const queryClient = useQueryClient();
  return useArchiveSkill({
    mutation: {
      onSuccess: async () => {
        await invalidateSkills(queryClient);
        toast.success(t("skillArchived"));
      },
      onError: (error) => toast.error(performanceErrorMessage(error, errors)),
    },
  });
}

export function useSkillRestore() {
  const t = useTranslations("performance.toasts");
  const errors = useApiErrorTranslator("performance.errors");
  const queryClient = useQueryClient();
  return useRestoreSkill({
    mutation: {
      onSuccess: async () => {
        await invalidateSkills(queryClient);
        toast.success(t("skillRestored"));
      },
      onError: (error) => toast.error(performanceErrorMessage(error, errors)),
    },
  });
}

export type EmployeeSkillFieldName = "skill_id" | "root";
export type EmployeeSkillFieldErrors = Partial<Record<EmployeeSkillFieldName, string>>;

/**
 * Attach failures land on the skill picker; an archived/lost skill or a
 * duplicate row is something the user changes there. On edit the skill is
 * fixed, so the sentence belongs at the dialog root.
 */
export function employeeSkillFieldErrors(
  error: unknown,
  t: (key: PerformanceErrorKey) => string,
  { hasSkillField }: { hasSkillField: boolean },
): EmployeeSkillFieldErrors {
  const message = performanceErrorMessage(error, t);
  if (hasSkillField && error instanceof ApiError) {
    switch (error.code) {
      case "skill_archived":
      case "employee_skill_exists":
      case "skill_not_found":
        return { skill_id: message };
      default:
        break;
    }
  }
  return { root: message };
}

export function useEmployeeSkillCreate(employeeId: string) {
  const t = useTranslations("performance.toasts");
  const errors = useApiErrorTranslator("performance.errors");
  const queryClient = useQueryClient();
  return useRecordEmployeeSkill({
    mutation: {
      onSuccess: async () => {
        await invalidateEmployeeSkills(queryClient, employeeId);
        toast.success(t("employeeSkillAdded"));
      },
      onError: (error) => toast.error(performanceErrorMessage(error, errors)),
    },
  });
}

export function useEmployeeSkillUpdate(employeeId: string) {
  const t = useTranslations("performance.toasts");
  const errors = useApiErrorTranslator("performance.errors");
  const queryClient = useQueryClient();
  return useUpdateEmployeeSkill({
    mutation: {
      onSuccess: async () => {
        await invalidateEmployeeSkills(queryClient, employeeId);
        toast.success(t("employeeSkillUpdated"));
      },
      onError: (error) => toast.error(performanceErrorMessage(error, errors)),
    },
  });
}

export function useEmployeeSkillRemove(employeeId: string) {
  const t = useTranslations("performance.toasts");
  const errors = useApiErrorTranslator("performance.errors");
  const queryClient = useQueryClient();
  return useRemoveEmployeeSkill({
    mutation: {
      onSuccess: async () => {
        await invalidateEmployeeSkills(queryClient, employeeId);
        toast.success(t("employeeSkillRemoved"));
      },
      onError: (error) => toast.error(performanceErrorMessage(error, errors)),
    },
  });
}
