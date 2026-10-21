"use client";

/**
 * One payroll run: status, totals, timesheets and every payslip line. Finalize
 * is deliberately gated on `calculated` and confirmed (it is terminal); a
 * correction run is the only way to change a finalized month. The list and
 * detail queries poll while the worker is queued/calculating, so the transition
 * to `calculated` appears without a reload. Client leaf.
 */

import {
  CircleAlert,
  ClipboardList,
  Info,
  Plus,
  RotateCcw,
  ArrowLeft,
  CheckCircle2,
} from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { cn } from "cn";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { Spinner } from "@/components/ui/spinner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

import {
  employeeLabel,
  formatHours,
  formatMoney,
  formatPeriod,
  formatPayrollTimestamp,
  formatPayrollDate,
} from "../format";
import { usePayrollAccess } from "../hooks/use-payroll-access";
import { usePayrollConfig } from "../hooks/use-payroll-config";
import { usePayrollDirectory } from "../hooks/use-payroll-directory";
import {
  usePayrollRun,
  usePayrollRunFinalize,
  usePayrollRunRecalculate,
  useRunPayslips,
  useRunTimesheets,
} from "../hooks/use-payroll-runs";
import {
  canAddBonus,
  canFinalizeRun,
  canRecalculateRun,
  deductionKindLabel,
  isPayrollNotFoundError,
  isRunCalculated,
  payrollErrorMessage,
  payrollRunFailureMessage,
  payrollRunStatusLabel,
  payrollRunStatusVariant,
  payrollTotalsLabel,
  payslipStatusLabel,
  payslipStatusVariant,
} from "../labels";
import type { PayrollRunTotals, Payslip } from "../types";
import { BonusDialog } from "./BonusDialog";
import { TablePagination } from "./TablePagination";

const PAGE_SIZE = 100;

export function RunDetail({ runId }: { runId: string }) {
  const { canView, canOperate } = usePayrollAccess();
  const runQuery = usePayrollRun(runId, canView);
  const configQuery = usePayrollConfig(canView);
  const directory = usePayrollDirectory();

  const [timesheetPage, setTimesheetPage] = useState(1);
  const [payslipPage, setPayslipPage] = useState(1);
  const [expandedPayslip, setExpandedPayslip] = useState<string | null>(null);
  const [bonusOpen, setBonusOpen] = useState(false);
  const [finalizeOpen, setFinalizeOpen] = useState(false);
  const [finalizeError, setFinalizeError] = useState<string | null>(null);

  const run = runQuery.data?.status === 200 ? runQuery.data.data : null;
  const hasResults = run !== null && isRunCalculated(run.status);

  const timesheetsQuery = useRunTimesheets(
    runId,
    { page: timesheetPage, page_size: PAGE_SIZE },
    hasResults,
  );
  const payslipsQuery = useRunPayslips(
    runId,
    { page: payslipPage, page_size: PAGE_SIZE },
    hasResults,
  );
  const recalculate = usePayrollRunRecalculate(runId);
  const finalize = usePayrollRunFinalize(runId);

  const currency = configQuery.data?.status === 200 ? configQuery.data.data.currency : null;

  if (!canView) {
    return (
      <div className="flex items-center justify-center gap-2 px-6 py-10 text-sm text-muted-foreground">
        <Spinner className="size-4" />
        <p>Checking access…</p>
      </div>
    );
  }

  if (runQuery.isPending) {
    return (
      <section className="space-y-6">
        <BackLink />
        <PageHeader
          title="Payroll run"
          description="Simulated payroll — timesheets, payslip lines and totals for one run."
        />
        <div className="flex items-center justify-center gap-2 px-6 py-10 text-sm text-muted-foreground">
          <Spinner className="size-4" />
          <p>Loading payroll run…</p>
        </div>
      </section>
    );
  }

  if (runQuery.isError || run === null) {
    const notFound = isPayrollNotFoundError(runQuery.error);
    return (
      <section className="space-y-6">
        <BackLink />
        <PageHeader
          title="Payroll run"
          description="Simulated payroll — timesheets, payslip lines and totals for one run."
        />
        {notFound ? (
          <EmptyState
            icon={ClipboardList}
            message="This payroll run doesn’t exist or you don’t have access to it."
          />
        ) : (
          <Alert variant="destructive">
            <CircleAlert aria-hidden="true" />
            <AlertDescription>
              <p>Could not load this payroll run.</p>
              <Button
                variant="outline"
                size="sm"
                className="mt-2"
                onClick={() => void runQuery.refetch()}
              >
                Retry
              </Button>
            </AlertDescription>
          </Alert>
        )}
      </section>
    );
  }

  const skipped = run.skipped ?? [];
  const timesheets = timesheetsQuery.data?.status === 200 ? timesheetsQuery.data.data.items : [];
  const timesheetPageInfo =
    timesheetsQuery.data?.status === 200 ? timesheetsQuery.data.data.page : null;
  const payslips = payslipsQuery.data?.status === 200 ? payslipsQuery.data.data.items : [];
  const payslipPageInfo = payslipsQuery.data?.status === 200 ? payslipsQuery.data.data.page : null;

  const finalizable = canFinalizeRun(run.status);
  const recalculable = canRecalculateRun(run.status);
  const bonusAllowed = canAddBonus(run.status);
  const finalized = run.status === "finalized";

  async function onRecalculate() {
    try {
      await recalculate.mutateAsync({ id: runId });
    } catch {
      // The hook toast is the visible error; keep the page stable.
    }
  }

  async function onFinalize() {
    setFinalizeError(null);
    try {
      await finalize.mutateAsync({ id: runId });
      setFinalizeOpen(false);
    } catch (error) {
      setFinalizeError(payrollErrorMessage(error));
    }
  }

  return (
    <section className="space-y-6">
      <BackLink />
      <PageHeader
        title={formatPeriod(run.period)}
        description="Simulated payroll — timesheets, payslip lines and totals for this run."
      />

      <div className="flex flex-wrap items-center gap-3 text-sm text-muted-foreground">
        <Badge variant={payrollRunStatusVariant[run.status]}>
          {payrollRunStatusLabel[run.status]}
        </Badge>
        <span>
          Created <time dateTime={run.created_at}>{formatPayrollTimestamp(run.created_at)}</time>
        </span>
        {run.calculated_at ? (
          <span>
            Calculated{" "}
            <time dateTime={run.calculated_at}>{formatPayrollTimestamp(run.calculated_at)}</time>
          </span>
        ) : null}
        {run.finalized_at ? (
          <span>
            Finalized{" "}
            <time dateTime={run.finalized_at}>{formatPayrollTimestamp(run.finalized_at)}</time>
          </span>
        ) : null}
        {run.supersedes_run_id ? (
          <span>
            Correction for{" "}
            <Link
              href={`/payroll/runs/${run.supersedes_run_id}`}
              className="text-primary underline-offset-4 hover:underline"
            >
              the previous run
            </Link>
          </span>
        ) : null}
        {run.superseded_by_run_id ? (
          <span>
            Superseded by{" "}
            <Link
              href={`/payroll/runs/${run.superseded_by_run_id}`}
              className="text-primary underline-offset-4 hover:underline"
            >
              a later run
            </Link>
          </span>
        ) : null}
      </div>

      {canOperate ? (
        <div className="flex flex-wrap items-center gap-2">
          {!finalized && recalculable ? (
            <Button
              variant="outline"
              disabled={recalculate.isPending}
              onClick={() => void onRecalculate()}
            >
              <RotateCcw />
              Recalculate
            </Button>
          ) : null}
          {!finalized && bonusAllowed ? (
            <Button variant="outline" onClick={() => setBonusOpen(true)}>
              <Plus />
              Add bonus
            </Button>
          ) : null}
          {!finalized ? (
            <Button
              disabled={!finalizable || finalize.isPending}
              onClick={() => {
                setFinalizeError(null);
                setFinalizeOpen(true);
              }}
            >
              <CheckCircle2 />
              Finalize run
            </Button>
          ) : null}
          {!finalizable && !finalized ? (
            <p className="text-xs text-muted-foreground">
              Finalize becomes available once the calculation finishes.
            </p>
          ) : null}
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">
          View only — Finance, Admin and Super Admin run payroll.
        </p>
      )}

      {run.status === "failed" ? (
        <Alert variant="destructive">
          <CircleAlert aria-hidden="true" />
          <AlertTitle>Calculation failed</AlertTitle>
          <AlertDescription>
            <p>{payrollRunFailureMessage(run.failed_code)}</p>
          </AlertDescription>
        </Alert>
      ) : null}

      {skipped.length > 0 ? (
        <Alert>
          <Info aria-hidden="true" />
          <AlertTitle>
            {run.skipped_count} {run.skipped_count === 1 ? "employee" : "employees"} skipped
          </AlertTitle>
          <AlertDescription>
            <p>No salary record was effective by the period end, so no payslip was created.</p>
            <ul className="mt-2 space-y-1">
              {skipped.map((entry) => (
                <li key={entry.employee_id}>
                  {employeeLabel(entry.employee_id, directory.nameById)}{" "}
                  <span className="text-muted-foreground">— no salary record</span>
                </li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      ) : null}

      {run.totals ? <TotalsSummary totals={run.totals} currency={currency} /> : null}

      <section aria-labelledby="run-timesheets-title" className="flex flex-col gap-3">
        <div className="space-y-1">
          <h2 id="run-timesheets-title" className="text-lg leading-6 font-semibold tracking-tight">
            Timesheets
          </h2>
          <p className="max-w-2xl text-sm leading-5 text-pretty text-muted-foreground">
            Worked hours, HR-verified overtime and approved leave. Informational only — base pay is
            never prorated.
          </p>
        </div>

        <Card className="gap-0 overflow-hidden rounded-md border-border py-0 shadow-none">
          {!hasResults ? (
            <div className="p-4">
              <EmptyState
                icon={ClipboardList}
                message={
                  run.status === "failed"
                    ? "Recalculate the run to produce timesheets."
                    : "Timesheets appear after the calculation finishes."
                }
              />
            </div>
          ) : timesheetsQuery.isPending ? (
            <div className="flex items-center justify-center gap-2 px-6 py-10 text-sm text-muted-foreground">
              <Spinner className="size-4" />
              <p>Loading timesheets…</p>
            </div>
          ) : timesheetsQuery.isError ? (
            <div className="p-4">
              <Alert variant="destructive">
                <CircleAlert aria-hidden="true" />
                <AlertDescription>
                  <p>Could not load timesheets.</p>
                  <Button
                    variant="outline"
                    size="sm"
                    className="mt-2"
                    onClick={() => void timesheetsQuery.refetch()}
                  >
                    Retry
                  </Button>
                </AlertDescription>
              </Alert>
            </div>
          ) : timesheets.length === 0 ? (
            <div className="p-4">
              <EmptyState icon={ClipboardList} message="This run has no timesheets." />
            </div>
          ) : (
            <>
              <p className="px-4 py-2 text-xs text-muted-foreground sm:hidden">
                Swipe sideways to see every timesheet field.
              </p>
              <Table
                aria-label="Run timesheets"
                containerLabel="Run timesheets. Scroll horizontally to read all columns."
              >
                <TableHeader>
                  <TableRow className="hover:bg-transparent">
                    <TableHead className="pl-3">Employee</TableHead>
                    <TableHead className="text-right">Worked hours</TableHead>
                    <TableHead className="text-right">Overtime hours</TableHead>
                    <TableHead className="text-right">Leave days</TableHead>
                    <TableHead className="pr-3 text-right">Attendance days</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {timesheets.map((timesheet) => (
                    <TableRow key={timesheet.employee_id}>
                      <TableCell className="pl-3 font-medium">
                        {employeeLabel(timesheet.employee_id, directory.nameById)}
                      </TableCell>
                      <TableCell className="text-right font-mono tabular-nums">
                        {formatHours(timesheet.worked_hours)}
                      </TableCell>
                      <TableCell className="text-right font-mono tabular-nums">
                        {formatHours(timesheet.overtime_hours)}
                      </TableCell>
                      <TableCell className="text-right font-mono tabular-nums">
                        {timesheet.leave_days}
                      </TableCell>
                      <TableCell className="pr-3 text-right font-mono tabular-nums">
                        {timesheet.attendance_days}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              {timesheetPageInfo === null ? null : (
                <TablePagination
                  page={timesheetPageInfo}
                  label="timesheets"
                  onPageChange={setTimesheetPage}
                />
              )}
            </>
          )}
        </Card>
      </section>

      <section aria-labelledby="run-payslips-title" className="flex flex-col gap-3">
        <div className="space-y-1">
          <h2 id="run-payslips-title" className="text-lg leading-6 font-semibold tracking-tight">
            Payslips
          </h2>
          <p className="max-w-2xl text-sm leading-5 text-pretty text-muted-foreground">
            Every payslip is provisional until finalize. A correction run marks the earlier set
            superseded, still readable.
          </p>
        </div>

        <Card className="gap-0 overflow-hidden rounded-md border-border py-0 shadow-none">
          {!hasResults ? (
            <div className="p-4">
              <EmptyState
                icon={ClipboardList}
                message={
                  run.status === "failed"
                    ? "Recalculate the run to produce payslips."
                    : "Payslips appear after the calculation finishes."
                }
              />
            </div>
          ) : payslipsQuery.isPending ? (
            <div className="flex items-center justify-center gap-2 px-6 py-10 text-sm text-muted-foreground">
              <Spinner className="size-4" />
              <p>Loading payslips…</p>
            </div>
          ) : payslipsQuery.isError ? (
            <div className="p-4">
              <Alert variant="destructive">
                <CircleAlert aria-hidden="true" />
                <AlertDescription>
                  <p>Could not load payslips.</p>
                  <Button
                    variant="outline"
                    size="sm"
                    className="mt-2"
                    onClick={() => void payslipsQuery.refetch()}
                  >
                    Retry
                  </Button>
                </AlertDescription>
              </Alert>
            </div>
          ) : payslips.length === 0 ? (
            <div className="p-4">
              <EmptyState icon={ClipboardList} message="This run has no payslips." />
            </div>
          ) : (
            <>
              <p className="px-4 py-2 text-xs text-muted-foreground sm:hidden">
                Swipe sideways to see every payslip field.
              </p>
              <Table
                aria-label="Run payslips"
                containerLabel="Run payslips. Scroll horizontally to read all columns."
              >
                <TableHeader>
                  <TableRow className="hover:bg-transparent">
                    <TableHead className="pl-3">Employee</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead className="text-right">Base</TableHead>
                    <TableHead className="text-right">Overtime</TableHead>
                    <TableHead className="text-right">Bonuses</TableHead>
                    <TableHead className="text-right">Deductions</TableHead>
                    <TableHead className="text-right">Net</TableHead>
                    <TableHead className="pr-3 text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {payslips.map((payslip) => {
                    const expanded = expandedPayslip === payslip.id;
                    const deductionTotal = (payslip.deductions ?? []).reduce(
                      (sum, line) => sum + line.amount,
                      0,
                    );
                    return (
                      <PayslipRows
                        key={payslip.id}
                        payslip={payslip}
                        expanded={expanded}
                        currency={currency}
                        directory={directory.nameById}
                        deductionTotal={deductionTotal}
                        onToggle={() => setExpandedPayslip(expanded ? null : payslip.id)}
                      />
                    );
                  })}
                </TableBody>
              </Table>
              {payslipPageInfo === null ? null : (
                <TablePagination
                  page={payslipPageInfo}
                  label="payslips"
                  onPageChange={setPayslipPage}
                />
              )}
            </>
          )}
        </Card>
      </section>

      <BonusDialog
        open={bonusOpen}
        onOpenChange={setBonusOpen}
        runId={runId}
        payslips={payslips}
        currency={currency}
        directory={directory.nameById}
      />

      <Dialog open={finalizeOpen} onOpenChange={setFinalizeOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Finalize payroll run?</DialogTitle>
            <DialogDescription>
              Finalizing issues every payslip and can’t be undone. A correction run is the only way
              to change this month afterwards.
            </DialogDescription>
          </DialogHeader>
          {finalizeError ? (
            <p role="alert" className="flex items-start gap-2 text-sm text-destructive">
              <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
              {finalizeError}
            </p>
          ) : null}
          <DialogFooter>
            <Button variant="outline" onClick={() => setFinalizeOpen(false)}>
              Cancel
            </Button>
            <Button disabled={finalize.isPending} onClick={() => void onFinalize()}>
              {finalize.isPending ? "Finalizing…" : "Finalize payroll"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}

function BackLink() {
  return (
    <Button asChild variant="ghost" size="sm" className="w-fit">
      <Link href="/payroll">
        <ArrowLeft />
        Back to payroll
      </Link>
    </Button>
  );
}

function TotalsSummary({
  totals,
  currency,
}: {
  totals: PayrollRunTotals;
  currency: string | null;
}) {
  const keys: Array<keyof PayrollRunTotals> = [
    "base",
    "overtime_pay",
    "bonuses",
    "deductions",
    "net",
  ];

  return (
    <Card className="gap-0 overflow-hidden rounded-md border-border py-0 shadow-none">
      <dl className="grid grid-cols-2 gap-px bg-border sm:grid-cols-5">
        {keys.map((key) => (
          <div key={key} className="bg-card p-4">
            <dt className="text-xs text-muted-foreground">{payrollTotalsLabel[key]}</dt>
            <dd
              className={cn(
                "mt-1 font-mono tabular-nums",
                key === "net" ? "text-lg font-semibold" : "text-sm",
              )}
            >
              {currency === null ? "—" : formatMoney(totals[key], currency)}
            </dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}

function PayslipRows({
  payslip,
  expanded,
  currency,
  directory,
  deductionTotal,
  onToggle,
}: {
  payslip: Payslip;
  expanded: boolean;
  currency: string | null;
  directory: ReadonlyMap<string, string>;
  deductionTotal: number;
  onToggle: () => void;
}) {
  const employee = employeeLabel(payslip.employee_id, directory);
  return (
    <>
      <TableRow>
        <TableCell className="pl-3 font-medium">{employee}</TableCell>
        <TableCell>
          <Badge variant={payslipStatusVariant[payslip.status]}>
            {payslipStatusLabel[payslip.status]}
          </Badge>
        </TableCell>
        <TableCell className="text-right font-mono tabular-nums">
          {currency === null ? "—" : formatMoney(payslip.base, currency)}
        </TableCell>
        <TableCell className="text-right font-mono tabular-nums">
          {currency === null ? "—" : formatMoney(payslip.overtime_pay, currency)}
        </TableCell>
        <TableCell className="text-right font-mono tabular-nums">
          {currency === null ? "—" : formatMoney(payslip.bonuses, currency)}
        </TableCell>
        <TableCell className="text-right font-mono tabular-nums">
          {currency === null ? "—" : formatMoney(deductionTotal, currency)}
        </TableCell>
        <TableCell className="text-right font-mono tabular-nums">
          {currency === null ? "—" : formatMoney(payslip.net, currency)}
        </TableCell>
        <TableCell className="pr-3 text-right">
          <Button
            variant="ghost"
            size="xs"
            className="min-h-11 sm:min-h-7"
            aria-expanded={expanded}
            aria-label={`${expanded ? "Hide" : "Show"} payslip lines for ${employee}`}
            onClick={onToggle}
          >
            {expanded ? "Hide lines" : "Show lines"}
          </Button>
        </TableCell>
      </TableRow>
      {expanded ? (
        <TableRow className="hover:bg-transparent">
          <TableCell colSpan={8} className="bg-muted/30 whitespace-normal">
            <div className="space-y-2 py-1">
              <p className="text-xs font-medium">Deduction lines</p>
              {(payslip.deductions ?? []).length === 0 ? (
                <p className="text-sm text-muted-foreground">No deduction lines.</p>
              ) : (
                <ul className="space-y-1">
                  {(payslip.deductions ?? []).map((line) => (
                    <li
                      key={line.code}
                      className="flex flex-wrap items-baseline justify-between gap-2 border-b border-border/60 py-1 last:border-0"
                    >
                      <span className="text-sm">
                        <span className="font-mono text-xs text-muted-foreground">{line.code}</span>{" "}
                        {line.name}{" "}
                        <span className="text-muted-foreground">
                          ({deductionKindLabel[line.kind]}:{" "}
                          {line.kind === "percent_of_base"
                            ? `${formatHours(line.value)}% of base`
                            : currency === null
                              ? formatHours(line.value)
                              : formatMoney(line.value, currency)}
                          )
                        </span>
                      </span>
                      <span className="font-mono text-sm tabular-nums">
                        {currency === null ? "—" : formatMoney(line.amount, currency)}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
              <p className="text-xs text-muted-foreground">
                Issued{" "}
                {payslip.issued_at === null || payslip.issued_at === undefined
                  ? "—"
                  : formatPayrollDate(payslip.issued_at)}
              </p>
            </div>
          </TableCell>
        </TableRow>
      ) : null}
    </>
  );
}
