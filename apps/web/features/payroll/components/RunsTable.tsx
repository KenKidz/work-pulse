"use client";

/**
 * The run history and entry point of the workspace. A queued/calculating run
 * makes the list poll (`usePayrollRuns`), so the worker's progress appears
 * without a reload; Review opens the run detail where finalize lives. Client
 * leaf.
 */

import { CircleAlert, FileSearch, Plus } from "lucide-react";
import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/empty-state";
import { Spinner } from "@/components/ui/spinner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

import { formatMoney, formatPeriod, formatPayrollTimestamp } from "../format";
import { usePayrollConfig } from "../hooks/use-payroll-config";
import { usePayrollRuns } from "../hooks/use-payroll-runs";
import { payrollRunStatusVariant } from "../labels";
import { PayrollSectionHeader } from "./PayrollSectionHeader";
import { RunFormDialog } from "./RunFormDialog";
import { TablePagination } from "./TablePagination";

const RUNS_SECTION_ID = "payroll-runs-title";
const RUNS_PAGE_SIZE = 20;

export function RunsTable({ canOperate }: { canOperate: boolean }) {
  const t = useTranslations("payroll");
  const common = useTranslations("common.actions");
  const runStatus = useTranslations("payroll.status.run");
  const locale = useLocale();
  const [page, setPage] = useState(1);
  const [dialogOpen, setDialogOpen] = useState(false);
  const runsQuery = usePayrollRuns({ page, page_size: RUNS_PAGE_SIZE });
  const configQuery = usePayrollConfig();

  const runs = runsQuery.data?.status === 200 ? runsQuery.data.data.items : [];
  const pageInfo = runsQuery.data?.status === 200 ? runsQuery.data.data.page : null;
  const currency = configQuery.data?.status === 200 ? configQuery.data.data.currency : null;

  return (
    <section aria-labelledby={RUNS_SECTION_ID} className="flex flex-col gap-4">
      <PayrollSectionHeader
        id={RUNS_SECTION_ID}
        title={t("runs.title")}
        description={t("runs.description")}
      >
        {canOperate ? (
          <Button onClick={() => setDialogOpen(true)}>
            <Plus />
            {t("runs.newRun")}
          </Button>
        ) : null}
      </PayrollSectionHeader>

      <Card className="gap-0 overflow-hidden rounded-md border-border py-0 shadow-none">
        {runsQuery.isPending ? (
          <div className="flex items-center justify-center gap-2 px-6 py-10 text-sm text-muted-foreground">
            <Spinner className="size-4" />
            <p>{t("runs.loading")}</p>
          </div>
        ) : runsQuery.isError ? (
          <div className="p-4">
            <Alert variant="destructive">
              <CircleAlert aria-hidden="true" />
              <AlertDescription>
                <p>{t("runs.loadError")}</p>
                <Button
                  variant="outline"
                  size="sm"
                  className="mt-2"
                  onClick={() => void runsQuery.refetch()}
                >
                  {common("retry")}
                </Button>
              </AlertDescription>
            </Alert>
          </div>
        ) : runs.length === 0 ? (
          <div className="p-4">
            <EmptyState
              icon={FileSearch}
              message={t("runs.empty")}
            />
          </div>
        ) : (
          <>
            <p className="px-4 py-2 text-xs text-muted-foreground sm:hidden">
              {t("runs.swipe")}
            </p>
            <Table
              aria-label={t("runs.label")}
              containerLabel={t("runs.containerLabel")}
            >
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="pl-3">{t("runs.columns.month")}</TableHead>
                  <TableHead>{t("runs.columns.status")}</TableHead>
                  <TableHead className="text-right">{t("runs.columns.employees")}</TableHead>
                  <TableHead className="text-right">{t("runs.columns.skipped")}</TableHead>
                  <TableHead className="text-right">{t("runs.columns.net")}</TableHead>
                  <TableHead>{t("runs.columns.created")}</TableHead>
                  <TableHead className="pr-3 text-right">{t("runs.columns.actions")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {runs.map((run) => (
                  <TableRow key={run.id}>
                    <TableCell className="pl-3 font-medium">
                      <span className="flex flex-wrap items-center gap-2">
                        {formatPeriod(run.period, locale)}
                        {run.supersedes_run_id !== null && run.supersedes_run_id !== undefined ? (
                          <Badge variant="outline">{t("runs.correction")}</Badge>
                        ) : null}
                      </span>
                    </TableCell>
                    <TableCell>
                      <span className="flex flex-wrap items-center gap-2">
                        <Badge variant={payrollRunStatusVariant[run.status]}>
                          {runStatus(run.status)}
                        </Badge>
                        {run.superseded_by_run_id !== null &&
                        run.superseded_by_run_id !== undefined ? (
                          <Badge variant="muted">{t("runs.superseded")}</Badge>
                        ) : null}
                      </span>
                    </TableCell>
                    <TableCell className="text-right font-mono tabular-nums">
                      {run.employee_count}
                    </TableCell>
                    <TableCell className="text-right font-mono tabular-nums">
                      {run.skipped_count}
                    </TableCell>
                    <TableCell className="text-right font-mono tabular-nums">
                      {run.totals === null || run.totals === undefined || currency === null
                        ? "—"
                        : formatMoney(run.totals.net, currency, locale)}
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      <time dateTime={run.created_at}>
                        {formatPayrollTimestamp(run.created_at, locale)}
                      </time>
                    </TableCell>
                    <TableCell className="pr-3 text-right">
                      <Button asChild variant="ghost" size="xs" className="min-h-11 sm:min-h-7">
                        <Link
                          href={`/payroll/runs/${run.id}`}
                          aria-label={`Review ${formatPeriod(run.period, locale)} run`}
                        >
                          {t("runs.review")}
                        </Link>
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            {pageInfo === null ? null : (
              <TablePagination page={pageInfo} label={t("runs.paginationLabel")} onPageChange={setPage} />
            )}
          </>
        )}
      </Card>

      <RunFormDialog open={dialogOpen} onOpenChange={setDialogOpen} />
    </section>
  );
}
