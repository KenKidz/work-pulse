"use client";

/**
 * Manager / HR / admin dashboard: exceptions first, then self-service, then
 * the live snapshot. One realtime snapshot feeds the counts, activity and
 * presence cards; the decision queue and the self-service rail fetch their own
 * scoped data. Every row deep-links to the surface that owns the action.
 */

import { ArrowRight, CircleAlert } from "lucide-react";
import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";
import { useMemo } from "react";
import { useGetCurrentEmployee } from "@workpulse/api-client";
import type { User } from "@workpulse/api-client/index.schemas";

import { BoardCountsPlaceholder, BoardCountsStrip } from "@/components/board-counts";
import { PageHeader } from "@/components/page-header";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";
import { ActivityFeed } from "@/features/attendance/components/ActivityFeed";
import { useEmployeeDirectory } from "@/features/employees/hooks/use-employee-directory";
import { isForbiddenError, useRealtimeBoard } from "@/features/realtime/hooks/use-realtime-board";
import { buildEmployeeDirectory, sortPresenceRows } from "@/features/realtime/utils";

import { formatLongDate, todayISO } from "../utils";
import { DashboardPresence } from "./DashboardPresence";
import { MyDayCard } from "./MyDayCard";
import { NeedsAttentionCard } from "./NeedsAttentionCard";

function PendingCard({ title, description }: { title: string; description: string }) {
  const common = useTranslations("common");
  return (
    <Card className="gap-0 rounded-md border-border py-0 shadow-none">
      <CardHeader className="px-5 pt-5 pb-4">
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <div className="flex min-h-48 items-center justify-center gap-2 px-5 pb-5 text-sm text-muted-foreground">
        <Spinner className="size-4" />
        <span>{common("states.loading")}</span>
      </div>
    </Card>
  );
}

function CountsPlaceholder() {
  const dashboard = useTranslations("dashboard");
  return (
    <BoardCountsPlaceholder
      note={
        <span className="flex items-center gap-2" role="status">
          <Spinner className="size-3.5" aria-hidden="true" />
          {dashboard("countsLoading")}
        </span>
      }
    />
  );
}

export function OperationsDashboard({ user }: { user: User }) {
  const t = useTranslations("dashboard");
  const activityT = useTranslations("realtime.activityRail");
  const presenceT = useTranslations("realtime.presence");
  const common = useTranslations("common.actions");
  const locale = useLocale();
  const today = todayISO();

  const boardQuery = useRealtimeBoard(true);
  const employeesQuery = useEmployeeDirectory();
  const meQuery = useGetCurrentEmployee({
    query: { enabled: user.employee_id !== null },
  });

  const directory = useMemo(
    () =>
      buildEmployeeDirectory(
        employeesQuery.data ?? [],
        meQuery.data?.status === 200 ? meQuery.data.data : null,
      ),
    [employeesQuery.data, meQuery.data],
  );

  const board = boardQuery.data?.status === 200 ? boardQuery.data.data : null;
  const presenceRows = useMemo(
    () => sortPresenceRows(board?.presence ?? [], directory),
    [board?.presence, directory],
  );
  const denied = boardQuery.isError && isForbiddenError(boardQuery.error);
  const hasEmployeeLink = user.employee_id !== null;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title={t("title")}
        description={t("managerDescription", { date: formatLongDate(today, locale) })}
        actions={
          hasEmployeeLink ? (
            <>
              <Button asChild variant="outline">
                <Link href="/leave">{t("actions.requestLeave")}</Link>
              </Button>
              <Button asChild>
                <Link href="/attendance">{t("actions.openAttendance")}</Link>
              </Button>
            </>
          ) : null
        }
      />

      {boardQuery.isPending ? (
        <CountsPlaceholder />
      ) : boardQuery.isError ? (
        <Alert variant="destructive">
          <CircleAlert aria-hidden="true" />
          <AlertTitle>{denied ? t("countsDeniedTitle") : t("countsErrorTitle")}</AlertTitle>
          <AlertDescription>
            <p>{denied ? t("countsDenied") : t("countsError")}</p>
            {!denied ? (
              <Button
                variant="outline"
                size="sm"
                className="mt-2"
                onClick={() => void boardQuery.refetch()}
              >
                {common("retry")}
              </Button>
            ) : null}
          </AlertDescription>
        </Alert>
      ) : board ? (
        <BoardCountsStrip counts={board.counts} note={t("snapshotNote")} />
      ) : null}

      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1.65fr)_minmax(20rem,1fr)]">
        <NeedsAttentionCard directory={directory} role={user.role} />
        <MyDayCard user={user} />
      </div>

      {boardQuery.isPending || board ? (
        <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1.65fr)_minmax(20rem,1fr)]">
          {board ? (
            <ActivityFeed
              activity={board.activity}
              directory={directory}
              limit={6}
              action={
                <Link
                  href="/command-center"
                  className="flex min-h-8 items-center gap-1 text-xs font-medium text-primary underline-offset-4 hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
                >
                  {t("actions.commandCenter")}{" "}
                  <ArrowRight className="size-3.5" aria-hidden="true" />
                </Link>
              }
            />
          ) : (
            <PendingCard title={activityT("title")} description={activityT("description")} />
          )}

          {board ? (
            <DashboardPresence rows={presenceRows} directory={directory} limit={6} />
          ) : (
            <PendingCard title={presenceT("title")} description={presenceT("description")} />
          )}
        </div>
      ) : null}
    </div>
  );
}
