"use client";

/**
 * The employee's own leave + overtime trail, newest first. Two scoped lists
 * merged for one reading order; the owning surfaces keep withdrawal and
 * decision logic. No names are shown — the list is always the caller's own.
 */

import { ChevronRight, Inbox } from "lucide-react";
import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";
import { useMemo } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/empty-state";
import { Spinner } from "@/components/ui/spinner";
import { useLeaveRequests } from "@/features/leave/hooks/use-leave";
import { leaveStatusVariant } from "@/features/leave/labels";
import type { LeaveRequest } from "@/features/leave/types";
import { useOvertimeRequests } from "@/features/overtime/hooks/use-overtime";
import { overtimeStatusVariant } from "@/features/overtime/labels";
import type { OvertimeRequest } from "@/features/overtime/types";

import { formatDate, formatDateRange, formatHours } from "../utils";

const REQUEST_LIMIT = 5;

type Row =
  | {
      kind: "leave";
      request: LeaveRequest;
    }
  | {
      kind: "overtime";
      request: OvertimeRequest;
    };

export function MyRequestsCard({ enabled }: { enabled: boolean }) {
  const t = useTranslations("dashboard.requests");
  const leaveT = useTranslations("leave");
  const overtimeT = useTranslations("overtime");
  const leaveStatus = useTranslations("leave.status");
  const overtimeStatus = useTranslations("overtime.status");
  const units = useTranslations("attendance.units");
  const common = useTranslations("common.actions");
  const locale = useLocale();

  const leaveQuery = useLeaveRequests({ page: 1, page_size: REQUEST_LIMIT }, enabled);
  const overtimeQuery = useOvertimeRequests({ page: 1, page_size: REQUEST_LIMIT }, { enabled });

  const rows = useMemo<Row[]>(() => {
    const leaveItems = leaveQuery.data?.status === 200 ? leaveQuery.data.data.items : [];
    const overtimeItems = overtimeQuery.data?.status === 200 ? overtimeQuery.data.data.items : [];
    const merged: Row[] = [
      ...leaveItems.map((request) => ({ kind: "leave" as const, request })),
      ...overtimeItems.map((request) => ({ kind: "overtime" as const, request })),
    ];
    return merged
      .sort((a, b) => b.request.created_at.localeCompare(a.request.created_at))
      .slice(0, REQUEST_LIMIT);
  }, [leaveQuery.data, overtimeQuery.data]);

  const isPending = leaveQuery.isPending || overtimeQuery.isPending;
  const isError = leaveQuery.isError || overtimeQuery.isError;

  return (
    <Card
      data-testid="my-requests"
      className="gap-0 overflow-hidden rounded-md border-border py-0 shadow-none"
    >
      <CardHeader className="px-5 pt-5 pb-3">
        <CardTitle>{t("title")}</CardTitle>
        <CardDescription>{t("description")}</CardDescription>
      </CardHeader>

      {isPending ? (
        <div className="flex min-h-40 items-center justify-center gap-2 px-5 pb-5 text-sm text-muted-foreground">
          <Spinner className="size-4" />
          <span>{t("loading")}</span>
        </div>
      ) : isError ? (
        <div className="px-5 pb-5">
          <Alert variant="destructive">
            <AlertDescription>
              <p>{t("error")}</p>
              <Button
                variant="outline"
                size="sm"
                className="mt-2"
                onClick={() => {
                  void leaveQuery.refetch();
                  void overtimeQuery.refetch();
                }}
              >
                {common("retry")}
              </Button>
            </AlertDescription>
          </Alert>
        </div>
      ) : rows.length === 0 ? (
        <div className="px-5 pb-5">
          <EmptyState icon={Inbox} message={t("empty")} />
          <div className="mt-3 flex justify-center">
            <Button asChild variant="outline" size="sm" className="min-h-11 sm:min-h-8">
              <Link href="/leave">{t("requestLeave")}</Link>
            </Button>
          </div>
        </div>
      ) : (
        <>
          <ul className="flex flex-col">
            {rows.map((row) => {
              if (row.kind === "leave") {
                const request = row.request;
                return (
                  <li key={`leave-${request.id}`}>
                    <Link
                      href="/leave"
                      className="flex min-h-14 items-center gap-3 border-b border-border px-5 py-3 outline-none last:border-b-0 hover:bg-muted/50 focus-visible:ring-[3px] focus-visible:ring-ring/50"
                    >
                      <Badge variant="outline">{t("leave")}</Badge>
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-medium">
                          {leaveT(`types.${request.leave_type_code}`)}
                        </p>
                        <p className="mt-0.5 truncate text-xs text-muted-foreground">
                          {formatDateRange(request.start_date, request.end_date, locale)} ·{" "}
                          {leaveT("request.inclusiveDays", { days: request.days })}
                        </p>
                      </div>
                      <Badge variant={leaveStatusVariant[request.status]}>
                        {leaveStatus(request.status)}
                      </Badge>
                      <ChevronRight className="size-4 text-muted-foreground" aria-hidden="true" />
                    </Link>
                  </li>
                );
              }
              const request = row.request;
              return (
                <li key={`overtime-${request.id}`}>
                  <Link
                    href="/overtime"
                    className="flex min-h-14 items-center gap-3 border-b border-border px-5 py-3 outline-none last:border-b-0 hover:bg-muted/50 focus-visible:ring-[3px] focus-visible:ring-ring/50"
                  >
                    <Badge variant="outline">{t("overtime")}</Badge>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium">
                        {request.reason?.trim() || overtimeT("pageTitle")}
                      </p>
                      <p className="mt-0.5 truncate text-xs text-muted-foreground">
                        {formatDate(request.work_date, locale)} ·{" "}
                        {`${formatHours(request.hours, locale)} ${units("hourShort")}`}
                      </p>
                    </div>
                    <Badge variant={overtimeStatusVariant[request.status]}>
                      {overtimeStatus(request.status)}
                    </Badge>
                    <ChevronRight className="size-4 text-muted-foreground" aria-hidden="true" />
                  </Link>
                </li>
              );
            })}
          </ul>
          <div className="flex items-center justify-end gap-4 border-t border-border px-5 py-2.5 text-xs">
            <Link
              href="/leave"
              className="inline-flex min-h-8 items-center font-medium text-primary underline-offset-4 hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
            >
              {t("viewLeave")}
            </Link>
            <Link
              href="/overtime"
              className="inline-flex min-h-8 items-center font-medium text-primary underline-offset-4 hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
            >
              {t("viewOvertime")}
            </Link>
          </div>
        </>
      )}
    </Card>
  );
}
