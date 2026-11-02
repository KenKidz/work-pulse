"use client";

/**
 * Deduction-line hooks. The list is a bare array (the contract pins no
 * pagination); writes invalidate it and toast the mapped outcome.
 */

import { useQueryClient } from "@tanstack/react-query";
import {
  useCreatePayrollDeduction,
  useListPayrollDeductions,
  useUpdatePayrollDeduction,
} from "@workpulse/api-client";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { useApiErrorTranslator } from "@/lib/api-errors";

import { invalidatePayrollDeductions } from "../api";
import { payrollErrorMessage } from "../errors";

export { deductionFieldErrors } from "../errors";
export type { DeductionFieldErrors, DeductionFieldName } from "../errors";

export function usePayrollDeductions() {
  return useListPayrollDeductions();
}

export function usePayrollDeductionCreate() {
  const t = useTranslations("payroll.toasts");
  const errors = useApiErrorTranslator("payroll.errors");
  const queryClient = useQueryClient();
  return useCreatePayrollDeduction({
    mutation: {
      onSuccess: async () => {
        await invalidatePayrollDeductions(queryClient);
        toast.success(t("deductionCreated"));
      },
      onError: (error) => toast.error(payrollErrorMessage(error, errors)),
    },
  });
}

export function usePayrollDeductionUpdate() {
  const t = useTranslations("payroll.toasts");
  const errors = useApiErrorTranslator("payroll.errors");
  const queryClient = useQueryClient();
  return useUpdatePayrollDeduction({
    mutation: {
      onSuccess: async () => {
        await invalidatePayrollDeductions(queryClient);
        toast.success(t("deductionUpdated"));
      },
      onError: (error) => toast.error(payrollErrorMessage(error, errors)),
    },
  });
}
