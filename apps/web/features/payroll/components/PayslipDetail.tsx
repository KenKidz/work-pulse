"use client";

/**
 * One of the caller's own payslips, line by line. The API returns a payslip
 * only to its owner (or a company-wide payroll viewer), so a foreign or
 * unknown id is the same not-found state — existence is never leaked. Amounts
 * carry no currency symbol because employees cannot read the tenant payroll
 * config; the page states the tenant currency instead. Client leaf.
 */

import { ArrowLeft, CircleAlert, Info, ReceiptText } from "lucide-react";
import Link from "next/link";
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

import { formatAmount, formatHours, formatPayrollTimestamp, formatPeriod } from "../format";
import { useMyPayslip } from "../hooks/use-my-payslips";
import {
  deductionKindLabel,
  isPayrollNotFoundError,
  payrollTotalsLabel,
  payslipStatusLabel,
  payslipStatusVariant,
} from "../labels";
import type { PayrollRunTotals, PayslipDeductionLine } from "../types";

const SUMMARY_KEYS: Array<keyof PayrollRunTotals> = [
  "base",
  "overtime_pay",
  "bonuses",
  "deductions",
  "net",
];

export function PayslipDetail({ payslipId }: { payslipId: string }) {
  const payslipQuery = useMyPayslip(payslipId);

  if (payslipQuery.isPending) {
    return (
      <section className="space-y-6">
        <BackLink />
        <PageHeader
          title="Payslip"
          description="Simulated payroll — every line that makes up your net pay."
        />
        <div className="flex items-center justify-center gap-2 px-6 py-10 text-sm text-muted-foreground">
          <Spinner className="size-4" />
          <p>Loading payslip…</p>
        </div>
      </section>
    );
  }

  if (payslipQuery.isError || payslipQuery.data?.status !== 200) {
    const notFound = isPayrollNotFoundError(payslipQuery.error);
    return (
      <section className="space-y-6">
        <BackLink />
        <PageHeader
          title="Payslip"
          description="Simulated payroll — every line that makes up your net pay."
        />
        {notFound ? (
          <EmptyState
            icon={ReceiptText}
            message="This payslip doesn’t exist or you don’t have access to it."
          />
        ) : (
          <Alert variant="destructive">
            <CircleAlert aria-hidden="true" />
            <AlertDescription>
              <p>Could not load this payslip.</p>
              <Button
                variant="outline"
                size="sm"
                className="mt-2"
                onClick={() => void payslipQuery.refetch()}
              >
                Retry
              </Button>
            </AlertDescription>
          </Alert>
        )}
      </section>
    );
  }

  const payslip = payslipQuery.data.data;
  const deductionLines = payslip.deductions ?? [];
  const deductionTotal = deductionLines.reduce((sum, line) => sum + line.amount, 0);
  const summary: Record<keyof PayrollRunTotals, number> = {
    base: payslip.base,
    overtime_pay: payslip.overtime_pay,
    bonuses: payslip.bonuses,
    deductions: deductionTotal,
    net: payslip.net,
  };

  return (
    <section className="space-y-6">
      <BackLink />
      <PageHeader
        title={`Payslip — ${formatPeriod(payslip.period)}`}
        description="Simulated payroll — every line that makes up your net pay."
      />

      <div className="flex flex-wrap items-center gap-3 text-sm text-muted-foreground">
        <Badge variant={payslipStatusVariant[payslip.status]}>
          {payslipStatusLabel[payslip.status]}
        </Badge>
        {payslip.issued_at === null || payslip.issued_at === undefined ? null : (
          <span>
            Issued{" "}
            <time dateTime={payslip.issued_at}>{formatPayrollTimestamp(payslip.issued_at)}</time>
          </span>
        )}
        <span>Amounts are in your organization’s payroll currency.</span>
      </div>

      {payslip.status === "superseded" ? (
        <Alert>
          <Info aria-hidden="true" />
          <AlertTitle>This payslip was superseded</AlertTitle>
          <AlertDescription>
            <p>A later correction run replaced it. It stays available for your records.</p>
          </AlertDescription>
        </Alert>
      ) : null}

      <SummaryCard summary={summary} />

      <DeductionLines lines={deductionLines} />
    </section>
  );
}

function BackLink() {
  return (
    <Button asChild variant="ghost" size="sm" className="w-fit">
      <Link href="/payroll/my-payslips">
        <ArrowLeft />
        Back to my payslips
      </Link>
    </Button>
  );
}

function SummaryCard({ summary }: { summary: Record<keyof PayrollRunTotals, number> }) {
  return (
    <Card className="gap-0 overflow-hidden rounded-md border-border py-0 shadow-none">
      <dl className="grid grid-cols-2 gap-px bg-border sm:grid-cols-5">
        {SUMMARY_KEYS.map((key) => (
          <div key={key} className="bg-card p-4">
            <dt className="text-xs text-muted-foreground">{payrollTotalsLabel[key]}</dt>
            <dd
              className={cn(
                "mt-1 font-mono tabular-nums",
                key === "net" ? "text-lg font-semibold" : "text-sm",
              )}
            >
              {formatAmount(summary[key])}
            </dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}

function DeductionLines({ lines }: { lines: PayslipDeductionLine[] }) {
  return (
    <section aria-labelledby="payslip-deductions-title" className="flex flex-col gap-3">
      <div className="space-y-1">
        <h2
          id="payslip-deductions-title"
          className="text-lg leading-6 font-semibold tracking-tight"
        >
          Deduction lines
        </h2>
        <p className="max-w-2xl text-sm leading-5 text-pretty text-muted-foreground">
          Every active tenant rule appears here as its own line — no hidden tax tables.
        </p>
      </div>

      <Card className="gap-0 overflow-hidden rounded-md border-border py-0 shadow-none">
        {lines.length === 0 ? (
          <div className="p-4">
            <EmptyState icon={ReceiptText} message="No deductions on this payslip." />
          </div>
        ) : (
          <>
            <p className="px-4 py-2 text-xs text-muted-foreground sm:hidden">
              Swipe sideways to see every deduction field.
            </p>
            <Table
              aria-label="Payslip deduction lines"
              containerLabel="Payslip deduction lines. Scroll horizontally to read all columns."
            >
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="pl-3">Code</TableHead>
                  <TableHead>Name</TableHead>
                  <TableHead>Kind</TableHead>
                  <TableHead className="text-right">Rule</TableHead>
                  <TableHead className="pr-3 text-right">Amount</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {lines.map((line) => (
                  <TableRow key={line.code}>
                    <TableCell className="pl-3 font-mono text-xs">{line.code}</TableCell>
                    <TableCell className="font-medium">{line.name}</TableCell>
                    <TableCell className="text-muted-foreground">
                      {deductionKindLabel[line.kind]}
                    </TableCell>
                    <TableCell className="text-right font-mono tabular-nums">
                      {line.kind === "percent_of_base"
                        ? `${formatHours(line.value)}% of base`
                        : formatAmount(line.value)}
                    </TableCell>
                    <TableCell className="pr-3 text-right font-mono tabular-nums">
                      {formatAmount(line.amount)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </>
        )}
      </Card>
    </section>
  );
}
