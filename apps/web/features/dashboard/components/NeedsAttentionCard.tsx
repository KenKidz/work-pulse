"use client";

/**
 * The dashboard's decision surface: pending leave and overtime in one place.
 * Rows deep-link to the owning workspace — the decision dialogs, notes and
 * self-approval guards stay in one implementation there, never duplicated
 * here. Names are joined from the directory; the lists carry ids only.
 */

import { ChevronRight, Inbox } from "lucide-react";
import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";
import type { Role } from "@workpulse/api-client/index.schemas";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/empty-state";
import { Spinner } from "@/components/ui/spinner";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  isForbiddenError as isLeaveForbidden,
  useLeaveRequests,
} from "@/features/leave/hooks/use-leave";
import type { LeaveRequest } from "@/features/leave/types";
import { useOvertimeRequests } from "@/features/overtime/hooks/use-overtime";
import {
  OVERTIME_HR_STEP_ROLES,
  isForbiddenError as isOvertimeForbidden,
} from "@/features/overtime/labels";
import type { OvertimeRequest } from "@/features/overtime/types";
import type { EmployeeDirectory } from "@/features/realtime/utils";
import { employeeDisplayName } from "@/features/realtime/utils";

import { formatDate, formatDateRange, formatHours, formatRelative, initials } from "../utils";

const ATTENTION_LIMIT = 4;

type RowProps = {
  href: string;
  name: string;
  kind: string;
  meta: string;
  age: string;
};

function RequestRow({ href, name, kind, meta, age }: RowProps) {
  return (
    <Link
      href={href}
      className="flex min-h-16 items-center gap-3 border-b border-border px-4 py-3 outline-none last:border-b-0 hover:bg-muted/50 focus-visible:ring-[3px] focus-visible:ring-ring/50"
    >
      <Avatar className="size-8">
        <AvatarFallback className="text-xs">{initials(name)}</AvatarFallback>
      </Avatar>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="truncate text-sm font-medium">{name}</span>
          <Badge variant="muted">{kind}</Badge>
        </div>
        <p className="mt-1 text-xs text-muted-foreground sm:truncate">{meta}</p>
      </div>
      <span className="hidden shrink-0 text-xs text-muted-foreground sm:block">{age}</span>
      <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
    </Link>
  );
}

function LeaveRows({
  requests,
  directory,
  locale,
}: {
  requests: LeaveRequest[];
  directory: EmployeeDirectory;
  locale: string;
}) {
  const t = useTranslations("leave");
  const attention = useTranslations("dashboard.attention");
  return (
    <>
      {requests.map((request) => {
        const name = employeeDisplayName(
          request.employee_id,
          directory,
          t("myRequests.employeeFallback", { id: request.employee_id.slice(-4) }),
        );
        return (
          <RequestRow
            key={request.id}
            href="/leave"
            name={name}
            kind={attention("leave")}
            meta={[
              t(`types.${request.leave_type_code}`),
              formatDateRange(request.start_date, request.end_date, locale),
              t("request.inclusiveDays", { days: request.days }),
            ].join(" · ")}
            age={formatRelative(request.created_at, locale)}
          />
        );
      })}
    </>
  );
}

function OvertimeRows({
  requests,
  directory,
  locale,
}: {
  requests: OvertimeRequest[];
  directory: EmployeeDirectory;
  locale: string;
}) {
  const t = useTranslations("overtime");
  const attention = useTranslations("dashboard.attention");
  const units = useTranslations("attendance.units");
  return (
    <>
      {requests.map((request) => {
        const name = employeeDisplayName(
          request.employee_id,
          directory,
          t("employeeFallback", { id: request.employee_id.slice(-4) }),
        );
        return (
          <RequestRow
            key={request.id}
            href="/overtime"
            name={name}
            kind={attention("overtime")}
            meta={[
              formatDate(request.work_date, locale),
              `${formatHours(request.hours, locale)} ${units("hourShort")}`,
            ].join(" · ")}
            age={formatRelative(request.created_at, locale)}
          />
        );
      })}
    </>
  );
}

function QueueBody({
  isPending,
  isError,
  denied,
  empty,
  emptyMessage,
  onRetry,
  children,
}: {
  isPending: boolean;
  isError: boolean;
  denied: boolean;
  empty: boolean;
  emptyMessage: string;
  onRetry: () => void;
  children: React.ReactNode;
}) {
  const t = useTranslations("dashboard.attention");
  const common = useTranslations("common.actions");

  if (isPending) {
    return (
      <div className="flex min-h-48 items-center justify-center gap-2 text-sm text-muted-foreground">
        <Spinner className="size-4" />
        <p>{t("loading")}</p>
      </div>
    );
  }
  if (isError) {
    return (
      <div className="p-4">
        <Alert variant="destructive">
          <AlertDescription>
            <p>{denied ? t("denied") : t("error")}</p>
            {!denied ? (
              <Button variant="outline" size="sm" className="mt-2" onClick={onRetry}>
                {common("retry")}
              </Button>
            ) : null}
          </AlertDescription>
        </Alert>
      </div>
    );
  }
  if (empty) {
    return (
      <div className="p-4">
        <EmptyState icon={Inbox} message={emptyMessage} />
      </div>
    );
  }
  return <>{children}</>;
}

export function NeedsAttentionCard({
  directory,
  role,
}: {
  directory: EmployeeDirectory;
  role: Role;
}) {
  const t = useTranslations("dashboard.attention");
  const locale = useLocale();
  const leaveQuery = useLeaveRequests({ status: "pending", page: 1, page_size: ATTENTION_LIMIT });
  // Managers clear the `requested` step; HR/admin own the verification step.
  // Showing only `requested` used to make the HR tab read zero while its queue
  // had work.
  const overtimeStatus = OVERTIME_HR_STEP_ROLES.includes(role) ? "manager_approved" : "requested";
  const overtimeQuery = useOvertimeRequests({
    status: overtimeStatus,
    page: 1,
    page_size: ATTENTION_LIMIT,
  });

  const leaveResponse = leaveQuery.data;
  const leaveItems = leaveResponse?.status === 200 ? leaveResponse.data.items : [];
  const leavePage = leaveResponse?.status === 200 ? leaveResponse.data.page : null;
  const overtimeResponse = overtimeQuery.data;
  const overtimeItems = overtimeResponse?.status === 200 ? overtimeResponse.data.items : [];
  const overtimePage = overtimeResponse?.status === 200 ? overtimeResponse.data.page : null;

  const leaveDenied = leaveQuery.isError && isLeaveForbidden(leaveQuery.error);
  const overtimeDenied = overtimeQuery.isError && isOvertimeForbidden(overtimeQuery.error);

  return (
    <Card
      data-testid="needs-attention"
      className="gap-0 overflow-hidden rounded-md border-border py-0 shadow-none"
    >
      <CardHeader className="px-5 pt-5 pb-3">
        <CardTitle>{t("title")}</CardTitle>
        <CardDescription>{t("description")}</CardDescription>
      </CardHeader>

      <Tabs defaultValue="leave" className="gap-0">
        <div className="border-b border-border px-4 py-2">
          <TabsList>
            <TabsTrigger value="leave">
              {t("leave")}
              {leavePage ? (
                <span className="ml-1.5 text-xs text-muted-foreground tabular-nums">
                  {leavePage.total_items}
                </span>
              ) : null}
            </TabsTrigger>
            <TabsTrigger value="overtime">
              {t("overtime")}
              {overtimePage ? (
                <span className="ml-1.5 text-xs text-muted-foreground tabular-nums">
                  {overtimePage.total_items}
                </span>
              ) : null}
            </TabsTrigger>
          </TabsList>
        </div>

        <TabsContent value="leave">
          <QueueBody
            isPending={leaveQuery.isPending}
            isError={leaveQuery.isError}
            denied={leaveDenied}
            empty={leaveItems.length === 0}
            emptyMessage={t("leaveEmpty")}
            onRetry={() => void leaveQuery.refetch()}
          >
            <LeaveRows requests={leaveItems} directory={directory} locale={locale} />
          </QueueBody>
          {leaveItems.length > 0 ? (
            <div className="flex items-center justify-between gap-3 border-t border-border px-4 py-2.5 text-xs text-muted-foreground">
              <span>
                {t("showing", {
                  shown: leaveItems.length,
                  total: leavePage?.total_items ?? leaveItems.length,
                })}
              </span>
              <Link
                href="/leave"
                className="inline-flex min-h-8 items-center font-medium text-primary underline-offset-4 hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
              >
                {t("openLeave")}
              </Link>
            </div>
          ) : null}
        </TabsContent>

        <TabsContent value="overtime">
          <QueueBody
            isPending={overtimeQuery.isPending}
            isError={overtimeQuery.isError}
            denied={overtimeDenied}
            empty={overtimeItems.length === 0}
            emptyMessage={t("overtimeEmpty")}
            onRetry={() => void overtimeQuery.refetch()}
          >
            <OvertimeRows requests={overtimeItems} directory={directory} locale={locale} />
          </QueueBody>
          {overtimeItems.length > 0 ? (
            <div className="flex items-center justify-between gap-3 border-t border-border px-4 py-2.5 text-xs text-muted-foreground">
              <span>
                {t("showing", {
                  shown: overtimeItems.length,
                  total: overtimePage?.total_items ?? overtimeItems.length,
                })}
              </span>
              <Link
                href="/overtime"
                className="inline-flex min-h-8 items-center font-medium text-primary underline-offset-4 hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
              >
                {t("openOvertime")}
              </Link>
            </div>
          ) : null}
        </TabsContent>
      </Tabs>
    </Card>
  );
}
