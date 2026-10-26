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
import { useLocale, useTranslations } from "next-intl";
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
import { isPayrollNotFoundError, payslipStatusVariant } from "../labels";
import type { PayrollRunTotals, PayslipDeductionLine } from "../types";

const SUMMARY_KEYS: Array<keyof PayrollRunTotals> = [
  "base",
  "overtime_pay",
  "bonuses",
  "deductions",
  "net",
];

export function PayslipDetail({ payslipId }: { payslipId: string }) {
  const t = useTranslations("payroll.payslipDetail");
  const statusT = useTranslations("payroll.status.payslip");
  const common = useTranslations("common.actions");
  const locale = useLocale();
  const payslipQuery = useMyPayslip(payslipId);

  if (payslipQuery.isPending) {
    return (
      <section className="space-y-6">
        <BackLink />
        <PageHeader title={t("title")} description={t("description")} />
        <div className="flex items-center justify-center gap-2 px-6 py-10 text-sm text-muted-foreground">
          <Spinner className="size-4" />
          <p>{t("loading")}</p>
        </div>
      </section>
    );
  }

  if (payslipQuery.isError || payslipQuery.data?.status !== 200) {
    const notFound = isPayrollNotFoundError(payslipQuery.error);
    return (
      <section className="space-y-6">
        <BackLink />
        <PageHeader title={t("title")} description={t("description")} />
        {notFound ? (
          <EmptyState icon={ReceiptText} message={t("notFound")} />
        ) : (
          <Alert variant="destructive">
            <CircleAlert aria-hidden="true" />
            <AlertDescription>
              <p>{t("loadError")}</p>
              <Button
                variant="outline"
                size="sm"
                className="mt-2"
                onClick={() => void payslipQuery.refetch()}
              >
                {common("retry")}
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
        title={t("runTitle", { period: formatPeriod(payslip.period, locale) })}
        description={t("description")}
      />

      <div className="flex flex-wrap items-center gap-3 text-sm text-muted-foreground">
        <Badge variant={payslipStatusVariant[payslip.status]}>
          {statusT(payslip.status)}
        </Badge>
        {payslip.issued_at === null || payslip.issued_at === undefined ? null : (
          <span>
            {t("issued", { time: formatPayrollTimestamp(payslip.issued_at, locale) })}
          </span>
        )}
        <span>{t("currencyNote")}</span>
      </div>

      {payslip.status === "superseded" ? (
        <Alert>
          <Info aria-hidden="true" />
          <AlertTitle>{t("supersededTitle")}</AlertTitle>
          <AlertDescription>
            <p>{t("supersededBody")}</p>
          </AlertDescription>
        </Alert>
      ) : null}

      <SummaryCard summary={summary} locale={locale} />

      <DeductionLines lines={deductionLines} locale={locale} />
    </section>
  );
}

function BackLink() {
  const t = useTranslations("payroll.payslipDetail");
  return (
    <Button asChild variant="ghost" size="sm" className="w-fit">
      <Link href="/payroll/my-payslips">
        <ArrowLeft />
        {t("backToMyPayslips")}
      </Link>
    </Button>
  );
}

function SummaryCard({
  summary,
  locale,
}: {
  summary: Record<keyof PayrollRunTotals, number>;
  locale: string;
}) {
  const totals = useTranslations("payroll.totals");

  return (
    <Card className="gap-0 overflow-hidden rounded-md border-border py-0 shadow-none">
      <dl className="grid grid-cols-2 gap-px bg-border sm:grid-cols-5">
        {SUMMARY_KEYS.map((key) => (
          <div key={key} className="bg-card p-4">
            <dt className="text-xs text-muted-foreground">{totals(key)}</dt>
            <dd
              className={cn(
                "mt-1 font-mono tabular-nums",
                key === "net" ? "text-lg font-semibold" : "text-sm",
              )}
            >
              {formatAmount(summary[key], locale)}
            </dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}

function DeductionLines({
  lines,
  locale,
}: {
  lines: PayslipDeductionLine[];
  locale: string;
}) {
  const t = useTranslations("payroll.payslipDetail");
  const kinds = useTranslations("payroll.kinds");

  return (
    <section aria-labelledby="payslip-deductions-title" className="flex flex-col gap-3">
      <div className="space-y-1">
        <h2
          id="payslip-deductions-title"
          className="text-lg leading-6 font-semibold tracking-tight"
        >
          {t("deductionsTitle")}
        </h2>
        <p className="max-w-2xl text-sm leading-5 text-pretty text-muted-foreground">
          {t("deductionsDescription")}
        </p>
      </div>

      <Card className="gap-0 overflow-hidden rounded-md border-border py-0 shadow-none">
        {lines.length === 0 ? (
          <div className="p-4">
            <EmptyState icon={ReceiptText} message={t("noDeductions")} />
          </div>
        ) : (
          <>
            <p className="px-4 py-2 text-xs text-muted-foreground sm:hidden">{t("swipe")}</p>
            <Table aria-label={t("tableLabel")} containerLabel={t("containerLabel")}>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="pl-3">{t("columns.code")}</TableHead>
                  <TableHead>{t("columns.name")}</TableHead>
                  <TableHead>{t("columns.kind")}</TableHead>
                  <TableHead className="text-right">{t("columns.rule")}</TableHead>
                  <TableHead className="pr-3 text-right">{t("columns.amount")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {lines.map((line) => (
                  <TableRow key={line.code}>
                    <TableCell className="pl-3 font-mono text-xs">{line.code}</TableCell>
                    <TableCell className="font-medium">{line.name}</TableCell>
                    <TableCell className="text-muted-foreground">{kinds(line.kind)}</TableCell>
                    <TableCell className="text-right font-mono tabular-nums">
                      {line.kind === "percent_of_base"
                        ? t("percentOfBase", { value: formatHours(line.value, locale) })
                        : formatAmount(line.value, locale)}
                    </TableCell>
                    <TableCell className="pr-3 text-right font-mono tabular-nums">
                      {formatAmount(line.amount, locale)}
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
