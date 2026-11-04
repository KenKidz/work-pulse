"use client";

/**
 * Employee / finance dashboard: the check-in is the primary action, followed
 * by the caller's own request trail. Balances and the latest payslip sit in
 * the right rail. Finance accounts without an employee link get the quick
 * actions instead of an unusable check-in form.
 */

import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";
import {
  useListAttendanceRecords,
  useListLeaveBalances,
  useListLeaveTypes,
  useListMyPayslips,
} from "@workpulse/api-client";
import type { User } from "@workpulse/api-client/index.schemas";

import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";
import { CheckInPanel } from "@/features/attendance/components/CheckInPanel";

import { currentYear, formatDate, formatLongDate, openRecordWindow, todayISO } from "../utils";
import { BalancePreview } from "./BalancePreview";
import { MyRequestsCard } from "./MyRequestsCard";
import { PayslipPreview } from "./PayslipPreview";
import { QuickActionsCard } from "./QuickActionsCard";

function LoadingCard({ title, description }: { title: string; description: string }) {
  const common = useTranslations("common");
  return (
    <Card className="gap-0 rounded-md border-border py-0 shadow-none">
      <CardHeader className="px-5 pt-5 pb-4">
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <div className="flex min-h-44 items-center justify-center gap-2 px-5 pb-5 text-sm text-muted-foreground">
        <Spinner className="size-4" />
        <span>{common("states.loading")}</span>
      </div>
    </Card>
  );
}

export function EmployeeDashboard({ user }: { user: User }) {
  const t = useTranslations("dashboard");
  const myDay = useTranslations("dashboard.myDay");
  const common = useTranslations("common.actions");
  const locale = useLocale();
  const today = todayISO();
  const window = openRecordWindow(today);
  const employeeId = user.employee_id ?? null;
  const enabled = employeeId !== null;

  const recordsQuery = useListAttendanceRecords(
    { from: window.from, to: window.to, page_size: 50 },
    { query: { enabled } },
  );
  const balancesQuery = useListLeaveBalances(
    { employee_id: employeeId ?? undefined, year: currentYear() },
    { query: { enabled } },
  );
  const typesQuery = useListLeaveTypes({ query: { enabled } });
  const payslipQuery = useListMyPayslips({ page: 1, page_size: 1 }, { query: { enabled } });

  const records = recordsQuery.data?.status === 200 ? recordsQuery.data.data.items : [];
  const openRecord =
    records.find(
      (record) =>
        (employeeId === null || record.employee_id === employeeId) &&
        (record.checked_out_at === null || record.checked_out_at === undefined),
    ) ?? null;
  const balances = balancesQuery.data?.status === 200 ? balancesQuery.data.data : [];
  const types = typesQuery.data?.status === 200 ? typesQuery.data.data : [];
  const payslip =
    payslipQuery.data?.status === 200 ? payslipQuery.data.data.items.at(0) : undefined;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title={t("title")}
        description={t("employeeDescription", { date: formatLongDate(today, locale) })}
        actions={
          enabled ? (
            <Button asChild variant="outline">
              <Link href="/leave">{t("actions.requestLeave")}</Link>
            </Button>
          ) : null
        }
      />

      {!enabled ? (
        <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1.65fr)_minmax(20rem,1fr)]">
          <Card className="gap-0 rounded-md border-border py-0 shadow-none">
            <CardHeader className="px-5 pt-5 pb-3">
              <CardTitle>{t("noEmployeeTitle")}</CardTitle>
              <CardDescription>{t("noEmployee")}</CardDescription>
            </CardHeader>
          </Card>
          <QuickActionsCard role={user.role} />
        </div>
      ) : (
        <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1.65fr)_minmax(20rem,1fr)]">
          <div className="flex flex-col gap-6">
            {recordsQuery.isPending ? (
              <LoadingCard title={t("attendanceTitle")} description={t("attendanceDescription")} />
            ) : recordsQuery.isError ? (
              <Card className="gap-0 rounded-md border-border py-0 shadow-none">
                <CardHeader className="px-5 pt-5 pb-3">
                  <CardTitle>{t("attendanceTitle")}</CardTitle>
                  <CardDescription>{myDay("statusError")}</CardDescription>
                </CardHeader>
                <CardContent className="px-5 pb-5">
                  <Button variant="outline" size="sm" onClick={() => void recordsQuery.refetch()}>
                    {common("retry")}
                  </Button>
                </CardContent>
              </Card>
            ) : (
              <CheckInPanel openRecord={openRecord} />
            )}
            <MyRequestsCard enabled={enabled} />
          </div>

          <div className="flex flex-col gap-6">
            <Card className="gap-0 rounded-md border-border py-0 shadow-none">
              <CardHeader className="flex flex-row items-start justify-between gap-3 px-5 pt-5 pb-3">
                <div className="space-y-1">
                  <CardTitle>{myDay("balances")}</CardTitle>
                  <CardDescription>{myDay("balancesDescription")}</CardDescription>
                </div>
                <Link
                  href="/leave"
                  className="inline-flex min-h-8 shrink-0 items-center text-xs font-medium text-primary underline-offset-4 hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
                >
                  {myDay("allBalances")}
                </Link>
              </CardHeader>
              <CardContent className="px-5 pb-5">
                <BalancePreview
                  balances={balances}
                  types={types}
                  isPending={balancesQuery.isPending || typesQuery.isPending}
                  isError={balancesQuery.isError || typesQuery.isError}
                />
              </CardContent>
            </Card>

            <Card className="gap-0 rounded-md border-border py-0 shadow-none">
              <CardHeader className="flex flex-row items-start justify-between gap-3 px-5 pt-5 pb-3">
                <div className="space-y-1">
                  <CardTitle>{myDay("payslip")}</CardTitle>
                  <CardDescription>{myDay("payslipDescription")}</CardDescription>
                </div>
                <Link
                  href="/payroll/my-payslips"
                  className="inline-flex min-h-8 shrink-0 items-center text-xs font-medium text-primary underline-offset-4 hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
                >
                  {myDay("viewPayslip")}
                </Link>
              </CardHeader>
              <CardContent className="px-5 pb-5">
                <PayslipPreview
                  payslip={payslip}
                  isPending={payslipQuery.isPending}
                  isError={payslipQuery.isError}
                />
              </CardContent>
            </Card>

            <p className="text-xs leading-5 text-muted-foreground">
              {t("employeeSnapshotNote", { date: formatDate(today, locale) })}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
