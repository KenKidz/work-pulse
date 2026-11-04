"use client";

/**
 * The dashboard's self-service rail: today's check-in state, leave balances
 * and the latest payslip, with deep links to the surfaces that own the
 * mutations. Accounts without an employee link get the quick-actions card
 * instead — a personal rail would be empty for them.
 */

import { CheckCircle2, LogIn } from "lucide-react";
import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";
import {
  useListAttendanceRecords,
  useListLeaveBalances,
  useListLeaveTypes,
  useListMyPayslips,
} from "@workpulse/api-client";
import type { User } from "@workpulse/api-client/index.schemas";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";

import { currentYear, formatClockTime, formatDate, openRecordWindow, todayISO } from "../utils";
import { BalancePreview } from "./BalancePreview";
import { PayslipPreview } from "./PayslipPreview";
import { QuickActionsCard } from "./QuickActionsCard";

export function MyDayCard({ user }: { user: User }) {
  const t = useTranslations("dashboard.myDay");
  const workModes = useTranslations("attendance.workModes");
  const locale = useLocale();
  const employeeId = user.employee_id ?? null;
  const today = todayISO();
  const window = openRecordWindow(today);
  const enabled = employeeId !== null;

  const recordsQuery = useListAttendanceRecords(
    { employee_id: employeeId ?? undefined, from: window.from, to: window.to, page_size: 50 },
    { query: { enabled } },
  );
  const balancesQuery = useListLeaveBalances(
    { employee_id: employeeId ?? undefined, year: currentYear() },
    { query: { enabled } },
  );
  const typesQuery = useListLeaveTypes({ query: { enabled } });
  const payslipQuery = useListMyPayslips({ page: 1, page_size: 1 }, { query: { enabled } });

  if (employeeId === null) {
    return <QuickActionsCard role={user.role} />;
  }

  const records = recordsQuery.data?.status === 200 ? recordsQuery.data.data.items : [];
  const openRecord =
    records.find(
      (record) =>
        record.employee_id === employeeId &&
        (record.checked_out_at === null || record.checked_out_at === undefined),
    ) ?? null;

  const types = typesQuery.data?.status === 200 ? typesQuery.data.data : [];
  const balances = balancesQuery.data?.status === 200 ? balancesQuery.data.data : [];

  const payslip =
    payslipQuery.data?.status === 200 ? payslipQuery.data.data.items.at(0) : undefined;

  return (
    <Card data-testid="my-day" className="gap-0 rounded-md border-border py-0 shadow-none">
      <CardHeader className="px-5 pt-5 pb-4">
        <CardTitle>{t("title")}</CardTitle>
        <CardDescription>{t("description", { date: formatDate(today, locale) })}</CardDescription>
      </CardHeader>
      <CardContent className="px-5 pb-5">
        <div className="flex flex-col divide-y divide-border">
          <div className="pb-4">
            {recordsQuery.isPending ? (
              <div className="flex min-h-16 items-center justify-center gap-2 rounded-sm border border-border text-sm text-muted-foreground">
                <Spinner className="size-4" />
                <span>{t("loading")}</span>
              </div>
            ) : recordsQuery.isError ? (
              <p className="rounded-sm border border-border p-3 text-sm text-muted-foreground">
                {t("statusError")}
              </p>
            ) : openRecord ? (
              <div className="flex flex-wrap items-center justify-between gap-3 rounded-sm border border-success/30 bg-success/5 p-3">
                <div className="flex items-center gap-3">
                  <span className="flex size-8 shrink-0 items-center justify-center rounded-sm bg-success/10 text-success">
                    <CheckCircle2 className="size-4" aria-hidden="true" />
                  </span>
                  <div>
                    <p className="text-sm font-medium">
                      {openRecord.workday === today
                        ? t("checkedIn", {
                            time: formatClockTime(openRecord.checked_in_at, locale),
                          })
                        : t("checkedInOn", {
                            date: formatDate(openRecord.workday, locale),
                            time: formatClockTime(openRecord.checked_in_at, locale),
                          })}
                    </p>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      {t("checkedInMeta", {
                        mode: workModes(openRecord.work_mode),
                        late: openRecord.late ? t("late") : t("onTime"),
                      })}
                    </p>
                  </div>
                </div>
                <Button asChild variant="outline" size="sm" className="min-h-11 sm:min-h-8">
                  <Link href="/attendance">{t("openAttendance")}</Link>
                </Button>
              </div>
            ) : (
              <div className="flex flex-wrap items-center justify-between gap-3 rounded-sm border border-border p-3">
                <div className="flex items-center gap-3">
                  <span className="flex size-8 shrink-0 items-center justify-center rounded-sm bg-muted text-muted-foreground">
                    <LogIn className="size-4" aria-hidden="true" />
                  </span>
                  <div>
                    <p className="text-sm font-medium">{t("notCheckedIn")}</p>
                    <p className="mt-0.5 text-xs text-muted-foreground">{t("notCheckedInHint")}</p>
                  </div>
                </div>
                <Button asChild size="sm" className="min-h-11 sm:min-h-8">
                  <Link href="/attendance">{t("checkIn")}</Link>
                </Button>
              </div>
            )}
          </div>

          <div className="py-4">
            <div className="mb-2 flex items-center justify-between gap-3">
              <p className="text-sm font-medium">{t("balances")}</p>
              <Link
                href="/leave"
                className="inline-flex min-h-8 items-center text-xs font-medium text-primary underline-offset-4 hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
              >
                {t("allBalances")}
              </Link>
            </div>
            <BalancePreview
              balances={balances}
              types={types}
              isPending={balancesQuery.isPending || typesQuery.isPending}
              isError={balancesQuery.isError || typesQuery.isError}
            />
          </div>

          <div className="pt-4">
            <div className="mb-2 flex items-center justify-between gap-3">
              <p className="text-sm font-medium">{t("payslip")}</p>
              <Link
                href="/payroll/my-payslips"
                className="inline-flex min-h-8 items-center text-xs font-medium text-primary underline-offset-4 hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
              >
                {t("viewPayslip")}
              </Link>
            </div>
            <PayslipPreview
              payslip={payslip}
              isPending={payslipQuery.isPending}
              isError={payslipQuery.isError}
            />
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
