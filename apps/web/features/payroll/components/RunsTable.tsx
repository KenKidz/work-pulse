"use client";

/**
 * The run history and entry point of the workspace. A queued/calculating run
 * makes the list poll (`usePayrollRuns`), so the worker's progress appears
 * without a reload; Review opens the run detail where finalize lives. Client
 * leaf.
 */

import { CircleAlert, FileSearch, Plus } from "lucide-react";
import Link from "next/link";
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
import { payrollRunStatusLabel, payrollRunStatusVariant } from "../labels";
import { PayrollSectionHeader } from "./PayrollSectionHeader";
import { RunFormDialog } from "./RunFormDialog";
import { TablePagination } from "./TablePagination";

const RUNS_SECTION_ID = "payroll-runs-title";
const RUNS_PAGE_SIZE = 20;

export function RunsTable({ canOperate }: { canOperate: boolean }) {
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
        title="Payroll runs"
        description="A closed month becomes timesheets and provisional payslips, then issues on finalize. A correction run supersedes an earlier finalized month."
      >
        {canOperate ? (
          <Button onClick={() => setDialogOpen(true)}>
            <Plus />
            New run
          </Button>
        ) : null}
      </PayrollSectionHeader>

      <Card className="gap-0 overflow-hidden rounded-md border-border py-0 shadow-none">
        {runsQuery.isPending ? (
          <div className="flex items-center justify-center gap-2 px-6 py-10 text-sm text-muted-foreground">
            <Spinner className="size-4" />
            <p>Loading payroll runs…</p>
          </div>
        ) : runsQuery.isError ? (
          <div className="p-4">
            <Alert variant="destructive">
              <CircleAlert aria-hidden="true" />
              <AlertDescription>
                <p>Could not load payroll runs.</p>
                <Button
                  variant="outline"
                  size="sm"
                  className="mt-2"
                  onClick={() => void runsQuery.refetch()}
                >
                  Retry
                </Button>
              </AlertDescription>
            </Alert>
          </div>
        ) : runs.length === 0 ? (
          <div className="p-4">
            <EmptyState
              icon={FileSearch}
              message="No payroll runs yet. Start one after the month closes and its salaries are in place."
            />
          </div>
        ) : (
          <>
            <p className="px-4 py-2 text-xs text-muted-foreground sm:hidden">
              Swipe sideways to see every run field.
            </p>
            <Table
              aria-label="Payroll runs"
              containerLabel="Payroll runs. Scroll horizontally to read all columns."
            >
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="pl-3">Month</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Employees</TableHead>
                  <TableHead className="text-right">Skipped</TableHead>
                  <TableHead className="text-right">Net</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead className="pr-3 text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {runs.map((run) => (
                  <TableRow key={run.id}>
                    <TableCell className="pl-3 font-medium">
                      <span className="flex flex-wrap items-center gap-2">
                        {formatPeriod(run.period)}
                        {run.supersedes_run_id !== null && run.supersedes_run_id !== undefined ? (
                          <Badge variant="outline">Correction</Badge>
                        ) : null}
                      </span>
                    </TableCell>
                    <TableCell>
                      <span className="flex flex-wrap items-center gap-2">
                        <Badge variant={payrollRunStatusVariant[run.status]}>
                          {payrollRunStatusLabel[run.status]}
                        </Badge>
                        {run.superseded_by_run_id !== null &&
                        run.superseded_by_run_id !== undefined ? (
                          <Badge variant="muted">Superseded</Badge>
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
                        : formatMoney(run.totals.net, currency)}
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      <time dateTime={run.created_at}>
                        {formatPayrollTimestamp(run.created_at)}
                      </time>
                    </TableCell>
                    <TableCell className="pr-3 text-right">
                      <Button asChild variant="ghost" size="xs" className="min-h-11 sm:min-h-7">
                        <Link
                          href={`/payroll/runs/${run.id}`}
                          aria-label={`Review ${formatPeriod(run.period)} run`}
                        >
                          Review
                        </Link>
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            {pageInfo === null ? null : (
              <TablePagination page={pageInfo} label="payroll runs" onPageChange={setPage} />
            )}
          </>
        )}
      </Card>

      <RunFormDialog open={dialogOpen} onOpenChange={setDialogOpen} />
    </section>
  );
}
