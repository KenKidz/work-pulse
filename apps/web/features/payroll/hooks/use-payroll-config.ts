"use client";

/**
 * Payroll configuration hooks — thin wrappers over the generated client.
 * A save refreshes the config cache and toasts the mapped outcome; a failure
 * never surfaces the raw code (`labels.ts` owns the sentences).
 */

import { useQueryClient } from "@tanstack/react-query";
import { useGetPayrollConfig, useUpdatePayrollConfig } from "@workpulse/api-client";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { invalidatePayrollConfig } from "../api";
import { payrollErrorMessage } from "../errors";

export function usePayrollConfig(enabled = true) {
  return useGetPayrollConfig({ query: { enabled } });
}

export function usePayrollConfigUpdate() {
  const t = useTranslations("payroll.toasts");
  const errors = useTranslations("payroll.errors");
  const queryClient = useQueryClient();
  return useUpdatePayrollConfig({
    mutation: {
      onSuccess: async () => {
        await invalidatePayrollConfig(queryClient);
        toast.success(t("configSaved"));
      },
      onError: (error) => toast.error(payrollErrorMessage(error, errors)),
    },
  });
}
