"use client";

/**
 * "My payslips" — the self-service list every role sees. The API returns only
 * the caller's issued and superseded payslips (a provisional draft never leaks
 * before finalize), so this table never needs a policy check of its own. A
 * superseded row is marked but stays readable: a correction run replaces the
 * current record, it does not erase history. Client leaf.
 */

import { CircleAlert, ReceiptText } from "lucide-react";
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

import { formatAmount, formatPeriod, formatPayrollTimestamp } from "../format";
import { useMyPayslips } from "../hooks/use-my-payslips";
import { payslipStatusLabel, payslipStatusVariant } from "../labels";
import { TablePagination } from "./TablePagination";

const MY_PAYSLIPS_PAGE_SIZE = 20;

export function MyPayslipsTable() {
  const [page, setPage] = useState(1);
  const payslipsQuery = useMyPayslips({ page, page_size: MY_PAYSLIPS_PAGE_SIZE });

  const payslips = payslipsQuery.data?.status === 200 ? payslipsQuery.data.data.items : [];
  const pageInfo = payslipsQuery.data?.status === 200 ? payslipsQuery.data.data.page : null;

  return (
    <Card className="gap-0 overflow-hidden rounded-md border-border py-0 shadow-none">
      {payslipsQuery.isPending ? (
        <div className="flex items-center justify-center gap-2 px-6 py-10 text-sm text-muted-foreground">
          <Spinner className="size-4" />
          <p>Loading your payslips…</p>
        </div>
      ) : payslipsQuery.isError ? (
        <div className="p-4">
          <Alert variant="destructive">
            <CircleAlert aria-hidden="true" />
            <AlertDescription>
              <p>Could not load your payslips.</p>
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
          <EmptyState
            icon={ReceiptText}
            message="No payslips yet. They appear here after a payroll run is finalized."
          />
        </div>
      ) : (
        <>
          <p className="px-4 py-2 text-xs text-muted-foreground sm:hidden">
            Swipe sideways to see every payslip field.
          </p>
          <Table
            aria-label="My payslips"
            containerLabel="My payslips. Scroll horizontally to read all columns."
          >
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead className="pl-3">Month</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Net pay</TableHead>
                <TableHead>Issued</TableHead>
                <TableHead className="pr-3 text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {payslips.map((payslip) => (
                <TableRow
                  key={payslip.id}
                  data-testid="my-payslip-row"
                  data-status={payslip.status}
                >
                  <TableCell className="pl-3 font-medium">{formatPeriod(payslip.period)}</TableCell>
                  <TableCell>
                    <Badge variant={payslipStatusVariant[payslip.status]}>
                      {payslipStatusLabel[payslip.status]}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-right font-mono tabular-nums">
                    {formatAmount(payslip.net)}
                  </TableCell>
                  <TableCell className="text-sm text-muted-foreground">
                    {payslip.issued_at === null || payslip.issued_at === undefined ? (
                      "—"
                    ) : (
                      <time dateTime={payslip.issued_at}>
                        {formatPayrollTimestamp(payslip.issued_at)}
                      </time>
                    )}
                  </TableCell>
                  <TableCell className="pr-3 text-right">
                    <Button asChild variant="ghost" size="xs" className="min-h-11 sm:min-h-7">
                      <Link
                        href={`/payroll/my-payslips/${payslip.id}`}
                        aria-label={`View ${payslipStatusLabel[payslip.status].toLowerCase()} ${formatPeriod(payslip.period)} payslip`}
                      >
                        View
                      </Link>
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {pageInfo === null ? null : (
            <TablePagination page={pageInfo} label="my payslips" onPageChange={setPage} />
          )}
        </>
      )}
    </Card>
  );
}
