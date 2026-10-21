/**
 * Re-exports of the generated payroll wire types — feature code imports from
 * here, never from the generated schemas file directly (employees/leave
 * pattern). `Employee`/`Role` are included for the payroll-directory join; the
 * wire carries identifiers only, so display names come from the employee
 * directory when the signed-in role may read it.
 */

export type {
  BonusCreate,
  Employee,
  ListPayrollRunsParams,
  ListRunPayslipsParams,
  ListRunTimesheetsParams,
  ListSalariesParams,
  Page,
  PayrollBonus,
  PayrollConfig,
  PayrollConfigUpdate,
  PayrollDeduction,
  PayrollDeductionCreate,
  PayrollDeductionKind,
  PayrollDeductionUpdate,
  PayrollRun,
  PayrollRunCreate,
  PayrollRunListPage,
  PayrollRunSkip,
  PayrollRunSkipReason,
  PayrollRunStatus,
  PayrollRunTotals,
  Payslip,
  PayslipDeductionLine,
  PayslipListPage,
  PayslipStatus,
  Role,
  Salary,
  SalaryCreate,
  SalaryListPage,
  Timesheet,
  TimesheetListPage,
} from "@workpulse/api-client/index.schemas";
