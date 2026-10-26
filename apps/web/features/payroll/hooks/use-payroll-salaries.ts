"use client";

/**
 * Salary-record hooks — append-only history, newest `effective_from` first.
 * Creating a record refreshes the history; a run is never recalculated
 * implicitly (the user recalculates explicitly).
 */

import { useQueryClient } from "@tanstack/react-query";
import { useCreateSalary, useListSalaries } from "@workpulse/api-client";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { invalidatePayrollSalaries } from "../api";
import { payrollErrorMessage } from "../errors";

export { salaryFieldErrors } from "../errors";
export type { SalaryFieldErrors, SalaryFieldName } from "../errors";
import type { ListSalariesParams } from "../types";

export function usePayrollSalaries(params?: ListSalariesParams) {
  return useListSalaries(params);
}

export function useSalaryCreate() {
  const t = useTranslations("payroll.toasts");
  const errors = useTranslations("payroll.errors");
  const queryClient = useQueryClient();
  return useCreateSalary({
    mutation: {
      onSuccess: async () => {
        await invalidatePayrollSalaries(queryClient);
        toast.success(t("salaryAdded"));
      },
      onError: (error) => toast.error(payrollErrorMessage(error, errors)),
    },
  });
}
