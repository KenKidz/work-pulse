"use client";

/**
 * Latest-payslip preview shared by the manager's My day rail and the employee
 * dashboard. One query shape, one period/net format, one status vocabulary —
 * the two dashboards cannot drift.
 */

import { ReceiptText } from "lucide-react";
import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";
import type { PayslipStatus } from "@workpulse/api-client/index.schemas";

import { Badge } from "@/components/ui/badge";
import { Spinner } from "@/components/ui/spinner";
import { formatAmount, formatPeriod } from "@/features/payroll/format";
import { payslipStatusVariant } from "@/features/payroll/labels";

type PayslipPreviewData = {
  period: string;
  net: number;
  status: PayslipStatus;
};

export function PayslipPreview({
  payslip,
  isPending,
  isError,
}: {
  payslip?: PayslipPreviewData;
  isPending: boolean;
  isError: boolean;
}) {
  const t = useTranslations("dashboard.myDay");
  const status = useTranslations("payroll.status.payslip");
  const locale = useLocale();

  if (isPending) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Spinner className="size-4" />
        <span>{t("loading")}</span>
      </div>
    );
  }
  if (isError) {
    return <p className="text-sm text-muted-foreground">{t("payslipError")}</p>;
  }
  if (payslip === undefined) {
    return <p className="text-sm text-muted-foreground">{t("noPayslip")}</p>;
  }

  return (
    <Link
      href="/payroll/my-payslips"
      className="flex items-center justify-between gap-3 rounded-sm border border-border p-3 outline-none hover:bg-muted/50 focus-visible:ring-[3px] focus-visible:ring-ring/50"
    >
      <span className="flex items-center gap-3">
        <span className="flex size-8 items-center justify-center rounded-sm bg-muted text-muted-foreground">
          <ReceiptText className="size-4" aria-hidden="true" />
        </span>
        <span>
          <span className="block text-sm font-medium">{formatPeriod(payslip.period, locale)}</span>
          <span className="block text-xs text-muted-foreground">
            {t("net", { amount: formatAmount(payslip.net, locale) })}
          </span>
        </span>
      </span>
      <Badge variant={payslipStatusVariant[payslip.status]}>{status(payslip.status)}</Badge>
    </Link>
  );
}
