"use client";

/**
 * The Finance/Admin payroll workspace. Runs lead (the recurring job), then the
 * inputs that shape them: configuration, deduction lines and salary records.
 * HR sees the same surfaces read-only; roles without company-wide payroll
 * visibility are redirected by `usePayrollAccess`. Client leaf.
 */

import { Spinner } from "@/components/ui/spinner";

import { DeductionLinesTable } from "./DeductionLinesTable";
import { PayrollConfigForm } from "./PayrollConfigForm";
import { RunsTable } from "./RunsTable";
import { SalaryForm } from "./SalaryForm";
import { usePayrollAccess } from "../hooks/use-payroll-access";

export function PayrollWorkspace() {
  const { canView, canOperate } = usePayrollAccess();

  if (!canView) {
    return (
      <div className="flex items-center justify-center gap-2 px-6 py-10 text-sm text-muted-foreground">
        <Spinner className="size-4" />
        <p>Checking access…</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-8">
      <RunsTable canOperate={canOperate} />
      <PayrollConfigForm canEdit={canOperate} />
      <DeductionLinesTable canEdit={canOperate} />
      <SalaryForm canEdit={canOperate} />
    </div>
  );
}
