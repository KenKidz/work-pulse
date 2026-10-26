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
import { useLocale, useTranslations } from "next-intl";
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
  isPayrollNotFoundError,
  isRunCalculated,
  payrollRunStatusVariant,
  payslipStatusVariant,
} from "../labels";
import { payrollErrorMessage, payrollRunFailureMessage } from "../errors";
import type { PayrollRunTotals, Payslip } from "../types";
import { BonusDialog } from "./BonusDialog";
import { TablePagination } from "./TablePagination";

const PAGE_SIZE = 100;

export function RunDetail({ runId }: { runId: string }) {
  const t = useTranslations("payroll.runDetail");
  const common = useTranslations("common.actions");
  const root = useTranslations("payroll");
  const runStatus = useTranslations("payroll.status.run");
  const errors = useTranslations("payroll.errors");
  const locale = useLocale();
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
        <p>{t("checkingAccess")}</p>
      </div>
    );
  }

  if (runQuery.isPending) {
    return (
      <section className="space-y-6">
        <BackLink />
        <PageHeader
          title={t("title")}
          description={t("description")}
        />
        <div className="flex items-center justify-center gap-2 px-6 py-10 text-sm text-muted-foreground">
          <Spinner className="size-4" />
          <p>{t("loading")}</p>
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
          title={t("title")}
          description={t("description")}
        />
        {notFound ? (
          <EmptyState
            icon={ClipboardList}
            message={t("notFound")}
          />
        ) : (
          <Alert variant="destructive">
            <CircleAlert aria-hidden="true" />
            <AlertDescription>
              <p>{t("loadError")}</p>
              <Button
                variant="outline"
                size="sm"
                className="mt-2"
                onClick={() => void runQuery.refetch()}
              >
                {common("retry")}
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
      setFinalizeError(payrollErrorMessage(error, errors));
    }
  }

  return (
    <section className="space-y-6">
      <BackLink />
      <PageHeader
        title={formatPeriod(run.period, locale)}
        description={t("description")}
      />

      <div className="flex flex-wrap items-center gap-3 text-sm text-muted-foreground">
        <Badge variant={payrollRunStatusVariant[run.status]}>
          {runStatus(run.status)}
        </Badge>
        <span>
          {t("created", {
            time: formatPayrollTimestamp(run.created_at, locale),
          })}
        </span>
        {run.calculated_at ? (
          <span>
            {t("calculated", {
              time: formatPayrollTimestamp(run.calculated_at, locale),
            })}
          </span>
        ) : null}
        {run.finalized_at ? (
          <span>{t("finalized", { time: formatPayrollTimestamp(run.finalized_at, locale) })}</span>
        ) : null}
        {run.supersedes_run_id ? (
          <span>
            {t("correctionFor", { link: t("previousRun") })}{" "}
            <Link
              href={`/payroll/runs/${run.supersedes_run_id}`}
              className="text-primary underline-offset-4 hover:underline"
            >
              {t("previousRun")}
            </Link>
          </span>
        ) : null}
        {run.superseded_by_run_id ? (
          <span>
            {t("supersededBy", { link: t("laterRun") })}{" "}
            <Link
              href={`/payroll/runs/${run.superseded_by_run_id}`}
              className="text-primary underline-offset-4 hover:underline"
            >
              {t("laterRun")}
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
              {t("recalculate")}
            </Button>
          ) : null}
          {!finalized && bonusAllowed ? (
            <Button variant="outline" onClick={() => setBonusOpen(true)}>
              <Plus />
              {t("addBonus")}
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
              {t("finalize")}
            </Button>
          ) : null}
          {!finalizable && !finalized ? (
            <p className="text-xs text-muted-foreground">
              {t("finalizeHint")}
            </p>
          ) : null}
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">
          {t("viewOnly")}
        </p>
      )}

      {run.status === "failed" ? (
        <Alert variant="destructive">
          <CircleAlert aria-hidden="true" />
          <AlertTitle>{t("failedTitle")}</AlertTitle>
          <AlertDescription>
            <p>{payrollRunFailureMessage(run.failed_code, t)}</p>
          </AlertDescription>
        </Alert>
      ) : null}

      {skipped.length > 0 ? (
        <Alert>
          <Info aria-hidden="true" />
          <AlertTitle>
            {t("skippedTitle", { count: run.skipped_count })}
          </AlertTitle>
          <AlertDescription>
            <p>{t("skippedDescription")}</p>
            <ul className="mt-2 space-y-1">
              {skipped.map((entry) => (
                <li key={entry.employee_id}>
                  {employeeLabel(
                    entry.employee_id,
                    directory.nameById,
                    root("employeeFallback", { id: entry.employee_id.slice(-4) }),
                  )}{" "}
                  <span className="text-muted-foreground">{t("skippedNoSalary")}</span>
                </li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      ) : null}

      {run.totals ? (
        <TotalsSummary totals={run.totals} currency={currency} locale={locale} />
      ) : null}

      <section aria-labelledby="run-timesheets-title" className="flex flex-col gap-3">
        <div className="space-y-1">
          <h2 id="run-timesheets-title" className="text-lg leading-6 font-semibold tracking-tight">
            {t("timesheetsTitle")}
          </h2>
          <p className="max-w-2xl text-sm leading-5 text-pretty text-muted-foreground">
            {t("timesheetsDescription")}
          </p>
        </div>

        <Card className="gap-0 overflow-hidden rounded-md border-border py-0 shadow-none">
          {!hasResults ? (
            <div className="p-4">
              <EmptyState
                icon={ClipboardList}
                message={
                  run.status === "failed" ? t("timesheetsFailed") : t("timesheetsPending")
                }
              />
            </div>
          ) : timesheetsQuery.isPending ? (
            <div className="flex items-center justify-center gap-2 px-6 py-10 text-sm text-muted-foreground">
              <Spinner className="size-4" />
              <p>{t("timesheetsLoading")}</p>
            </div>
          ) : timesheetsQuery.isError ? (
            <div className="p-4">
              <Alert variant="destructive">
                <CircleAlert aria-hidden="true" />
                <AlertDescription>
                  <p>{t("timesheetsLoadError")}</p>
                  <Button
                    variant="outline"
                    size="sm"
                    className="mt-2"
                    onClick={() => void timesheetsQuery.refetch()}
                  >
                    {common("retry")}
                  </Button>
                </AlertDescription>
              </Alert>
            </div>
          ) : timesheets.length === 0 ? (
            <div className="p-4">
              <EmptyState icon={ClipboardList} message={t("timesheetsEmpty")} />
            </div>
          ) : (
            <>
              <p className="px-4 py-2 text-xs text-muted-foreground sm:hidden">
                {t("timesheetsSwipe")}
              </p>
              <Table
                aria-label={t("timesheetsLabel")}
                containerLabel={t("timesheetsContainer")}
              >
                <TableHeader>
                  <TableRow className="hover:bg-transparent">
                    <TableHead className="pl-3">{t("timesheetColumns.employee")}</TableHead>
                    <TableHead className="text-right">{t("timesheetColumns.worked")}</TableHead>
                    <TableHead className="text-right">{t("timesheetColumns.overtime")}</TableHead>
                    <TableHead className="text-right">{t("timesheetColumns.leave")}</TableHead>
                    <TableHead className="pr-3 text-right">{t("timesheetColumns.attendance")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {timesheets.map((timesheet) => (
                    <TableRow key={timesheet.employee_id}>
                      <TableCell className="pl-3 font-medium">
                        {employeeLabel(
                          timesheet.employee_id,
                          directory.nameById,
                          root("employeeFallback", { id: timesheet.employee_id.slice(-4) }),
                        )}
                      </TableCell>
                      <TableCell className="text-right font-mono tabular-nums">
                        {formatHours(timesheet.worked_hours, locale)}
                      </TableCell>
                      <TableCell className="text-right font-mono tabular-nums">
                        {formatHours(timesheet.overtime_hours, locale)}
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
                  label={t("timesheetsPaginationLabel")}
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
            {t("payslipsTitle")}
          </h2>
          <p className="max-w-2xl text-sm leading-5 text-pretty text-muted-foreground">
            {t("payslipsDescription")}
          </p>
        </div>

        <Card className="gap-0 overflow-hidden rounded-md border-border py-0 shadow-none">
          {!hasResults ? (
            <div className="p-4">
              <EmptyState
                icon={ClipboardList}
                message={
                  run.status === "failed" ? t("payslipsFailed") : t("payslipsPending")
                }
              />
            </div>
          ) : payslipsQuery.isPending ? (
            <div className="flex items-center justify-center gap-2 px-6 py-10 text-sm text-muted-foreground">
              <Spinner className="size-4" />
              <p>{t("payslipsLoading")}</p>
            </div>
          ) : payslipsQuery.isError ? (
            <div className="p-4">
              <Alert variant="destructive">
                <CircleAlert aria-hidden="true" />
                <AlertDescription>
                  <p>{t("payslipsLoadError")}</p>
                  <Button
                    variant="outline"
                    size="sm"
                    className="mt-2"
                    onClick={() => void payslipsQuery.refetch()}
                  >
                    {common("retry")}
                  </Button>
                </AlertDescription>
              </Alert>
            </div>
          ) : payslips.length === 0 ? (
            <div className="p-4">
              <EmptyState icon={ClipboardList} message={t("payslipsEmpty")} />
            </div>
          ) : (
            <>
              <p className="px-4 py-2 text-xs text-muted-foreground sm:hidden">
                {t("payslipsSwipe")}
              </p>
              <Table
                aria-label={t("payslipsLabel")}
                containerLabel={t("payslipsContainer")}
              >
                <TableHeader>
                  <TableRow className="hover:bg-transparent">
                    <TableHead className="pl-3">{t("timesheetColumns.employee")}</TableHead>
                    <TableHead>{t("payslipColumns.status")}</TableHead>
                    <TableHead className="text-right">{t("payslipColumns.base")}</TableHead>
                    <TableHead className="text-right">{t("payslipColumns.overtime")}</TableHead>
                    <TableHead className="text-right">{t("payslipColumns.bonuses")}</TableHead>
                    <TableHead className="text-right">{t("payslipColumns.deductions")}</TableHead>
                    <TableHead className="text-right">{t("payslipColumns.net")}</TableHead>
                    <TableHead className="pr-3 text-right">{t("payslipColumns.actions")}</TableHead>
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
                  label={t("payslipsPaginationLabel")}
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
            <DialogTitle>{t("finalizeTitle")}</DialogTitle>
            <DialogDescription>{t("finalizeDescription")}</DialogDescription>
          </DialogHeader>
          {finalizeError ? (
            <p role="alert" className="flex items-start gap-2 text-sm text-destructive">
              <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
              {finalizeError}
            </p>
          ) : null}
          <DialogFooter>
            <Button variant="outline" onClick={() => setFinalizeOpen(false)}>
              {common("cancel")}
            </Button>
            <Button disabled={finalize.isPending} onClick={() => void onFinalize()}>
              {finalize.isPending ? t("finalizing") : t("finalizeConfirm")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}

function BackLink() {
  const t = useTranslations("payroll.runDetail");
  return (
    <Button asChild variant="ghost" size="sm" className="w-fit">
      <Link href="/payroll">
        <ArrowLeft />
        {t("backToPayroll")}
      </Link>
    </Button>
  );
}

function TotalsSummary({
  totals,
  currency,
  locale,
}: {
  totals: PayrollRunTotals;
  currency: string | null;
  locale: string;
}) {
  const labels = useTranslations("payroll.totals");
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
            <dt className="text-xs text-muted-foreground">{labels(key)}</dt>
            <dd
              className={cn(
                "mt-1 font-mono tabular-nums",
                key === "net" ? "text-lg font-semibold" : "text-sm",
              )}
            >
              {currency === null ? "—" : formatMoney(totals[key], currency, locale)}
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
  const t = useTranslations("payroll.runDetail");
  const statusT = useTranslations("payroll.status.payslip");
  const kinds = useTranslations("payroll.kinds");
  const root = useTranslations("payroll");
  const locale = useLocale();
  const employee = employeeLabel(
    payslip.employee_id,
    directory,
    root("employeeFallback", { id: payslip.employee_id.slice(-4) }),
  );
  return (
    <>
      <TableRow>
        <TableCell className="pl-3 font-medium">{employee}</TableCell>
        <TableCell>
          <Badge variant={payslipStatusVariant[payslip.status]}>
            {statusT(payslip.status)}
          </Badge>
        </TableCell>
        <TableCell className="text-right font-mono tabular-nums">
          {currency === null ? "—" : formatMoney(payslip.base, currency, locale)}
        </TableCell>
        <TableCell className="text-right font-mono tabular-nums">
          {currency === null ? "—" : formatMoney(payslip.overtime_pay, currency, locale)}
        </TableCell>
        <TableCell className="text-right font-mono tabular-nums">
          {currency === null ? "—" : formatMoney(payslip.bonuses, currency, locale)}
        </TableCell>
        <TableCell className="text-right font-mono tabular-nums">
          {currency === null ? "—" : formatMoney(deductionTotal, currency, locale)}
        </TableCell>
        <TableCell className="text-right font-mono tabular-nums">
          {currency === null ? "—" : formatMoney(payslip.net, currency, locale)}
        </TableCell>
        <TableCell className="pr-3 text-right">
          <Button
            variant="ghost"
            size="xs"
            className="min-h-11 sm:min-h-7"
            aria-expanded={expanded}
            aria-label={
              expanded
                ? t("hideLinesAria", { name: employee })
                : t("showLinesAria", { name: employee })
            }
            onClick={onToggle}
          >
            {expanded ? t("hideLines") : t("showLines")}
          </Button>
        </TableCell>
      </TableRow>
      {expanded ? (
        <TableRow className="hover:bg-transparent">
          <TableCell colSpan={8} className="bg-muted/30 whitespace-normal">
            <div className="space-y-2 py-1">
              <p className="text-xs font-medium">{t("deductionLines")}</p>
              {(payslip.deductions ?? []).length === 0 ? (
                <p className="text-sm text-muted-foreground">{t("noDeductionLines")}</p>
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
                          ({kinds(line.kind)}:{" "}
                          {line.kind === "percent_of_base"
                            ? t("percentOfBase", { value: formatHours(line.value, locale) })
                            : currency === null
                              ? formatHours(line.value, locale)
                              : formatMoney(line.value, currency, locale)}
                          )
                        </span>
                      </span>
                      <span className="font-mono text-sm tabular-nums">
                        {currency === null ? "—" : formatMoney(line.amount, currency, locale)}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
              <p className="text-xs text-muted-foreground">
                {t("issued", {
                  date:
                    payslip.issued_at === null || payslip.issued_at === undefined
                      ? "—"
                      : formatPayrollDate(payslip.issued_at, locale),
                })}
              </p>
            </div>
          </TableCell>
        </TableRow>
      ) : null}
    </>
  );
}
