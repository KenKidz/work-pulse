"use client";

/**
 * The attendance board — check-in/out and the day's numbers in the main
 * column, the activity rail beside them, then the auditable records and the
 * shift definitions. One explicit date keys the board and the records; names
 * are joined from the directory because the board contract carries ids only.
 * Client leaf: queries + mutations coordinate through the Query cache.
 */

import { ArrowRight, CircleAlert, Info } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { useMemo, useState } from "react";
import { cn } from "cn";

import { BoardCountsPlaceholder, BoardCountsStrip } from "@/components/board-counts";
import { PageHeader } from "@/components/page-header";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { buildEmployeeDirectory } from "@/features/realtime/utils";
import { useEmployeeDirectory } from "@/features/employees/hooks/use-employee-directory";
import { useAuth } from "@/features/auth/components/AuthGate";

import {
  isForbiddenError,
  useAttendanceBoard,
  useAttendanceRecords,
  useCurrentEmployee,
} from "../hooks/use-attendance";
import type { AttendanceRecord } from "../types";
import { ActivityFeed } from "./ActivityFeed";
import { AttendanceRecordsTable } from "./AttendanceRecordsTable";
import { CheckInPanel } from "./CheckInPanel";
import { ShiftsPanel } from "./ShiftsPanel";
import { todayISO } from "./dates";
import { ATTENDANCE_BOARD_ROLES, SHIFT_MANAGE_ROLES } from "../policies";

/** Read-only accounts still see the day's numbers and the auditable records. */
function ReadOnlyNotice() {
  const t = useTranslations("attendance");
  return (
    <Card className="gap-0 rounded-md border-border py-0 shadow-none">
      <div className="flex items-start gap-3 px-5 py-5">
        <span className="flex size-9 shrink-0 items-center justify-center rounded-sm bg-muted text-muted-foreground">
          <Info className="size-4" aria-hidden="true" />
        </span>
        <div className="min-w-0 space-y-1">
          <CardTitle>{t("noEmployeeTitle")}</CardTitle>
          <CardDescription>{t("noEmployee")}</CardDescription>
        </div>
      </div>
    </Card>
  );
}

function ActivityPlaceholder({ pending }: { pending: boolean }) {
  const t = useTranslations("attendance.activity");
  return (
    <Card className="gap-0 rounded-md border-border py-0 shadow-none">
      <CardHeader className="px-5 pt-5 pb-4">
        <CardTitle>{t("title")}</CardTitle>
        <CardDescription>{t("description")}</CardDescription>
      </CardHeader>
      <CardContent className="px-5 pb-5">
        <div
          className={cn(
            "flex items-center justify-center text-sm text-muted-foreground",
            pending && "min-h-[min(60dvh,36rem)]",
          )}
        >
          {pending ? <Spinner className="size-4" /> : <p>{t("unavailable")}</p>}
        </div>
      </CardContent>
      {pending ? (
        <div className="border-t border-border px-5 py-2.5">
          <span
            role="status"
            className="flex min-h-11 items-center text-xs text-muted-foreground sm:min-h-8"
          >
            {t("loading")}
          </span>
        </div>
      ) : null}
    </Card>
  );
}

export function AttendanceBoard() {
  const t = useTranslations("attendance");
  const common = useTranslations("common.actions");
  const { user } = useAuth();
  const canReadBoard = ATTENDANCE_BOARD_ROLES.includes(user.role);
  const canManageShifts = SHIFT_MANAGE_ROLES.includes(user.role);
  const canCheckIn = user.employee_id !== null;
  // The board date is explicit — never rely on the server's UTC default.
  const [date, setDate] = useState(todayISO());
  const boardParams = { date };

  const boardQuery = useAttendanceBoard(boardParams, canReadBoard);
  const recordsQuery = useAttendanceRecords({ from: date, to: date, page_size: 200 });
  const meQuery = useCurrentEmployee(canCheckIn);
  const employeesQuery = useEmployeeDirectory(canReadBoard);

  const boardResponse = boardQuery.data;
  const board = boardResponse && boardResponse.status === 200 ? boardResponse.data : null;

  const meResponse = meQuery.data;
  const me = meResponse && meResponse.status === 200 ? meResponse.data : null;
  const meId = me?.id ?? null;

  const directory = useMemo(
    () => buildEmployeeDirectory(employeesQuery.data ?? [], me),
    [employeesQuery.data, me],
  );

  // The actor's own open record for the visible date drives the panel state.
  const recordsResponse = recordsQuery.data;
  const dayRecords: AttendanceRecord[] =
    recordsResponse && recordsResponse.status === 200 ? recordsResponse.data.items : [];
  const openRecord =
    meId === null
      ? null
      : (dayRecords.find(
          (record) =>
            record.employee_id === meId &&
            record.workday === date &&
            (record.checked_out_at === null || record.checked_out_at === undefined),
        ) ?? null);

  const boardDenied = boardQuery.isError && isForbiddenError(boardQuery.error);
  const isToday = date === todayISO();

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-2">
        <PageHeader
          title={t("title")}
          description={canCheckIn ? t("description") : t("descriptionReadOnly")}
          actions={
            canReadBoard || canCheckIn ? (
              <div className="flex items-end gap-2">
                <div className="grid gap-1.5">
                  <Label htmlFor="board-date" className="text-xs font-medium text-muted-foreground">
                    {t("date")}
                  </Label>
                  <Input
                    id="board-date"
                    type="date"
                    aria-describedby="board-date-description"
                    className="min-h-11 w-40 tabular-nums sm:w-44"
                    value={date}
                    onChange={(event) => setDate(event.target.value || todayISO())}
                  />
                </div>
                <Button
                  variant="outline"
                  className="min-h-11 px-4"
                  disabled={isToday}
                  onClick={() => setDate(todayISO())}
                >
                  {t("today")}
                </Button>
              </div>
            ) : null
          }
        />
        {canReadBoard ? (
          <p
            id="board-date-description"
            className="max-w-xl text-xs leading-5 text-muted-foreground"
          >
            {t("boardDateHint")}
          </p>
        ) : canCheckIn ? (
          <p
            id="board-date-description"
            className="max-w-xl text-xs leading-5 text-muted-foreground"
          >
            {t("selfDateHint")}
          </p>
        ) : null}
      </div>

      {canReadBoard ? (
        boardQuery.isError || boardDenied ? (
          <>
            <Alert variant="destructive">
              <CircleAlert aria-hidden="true" />
              <AlertTitle>{boardDenied ? t("deniedTitle") : t("loadErrorTitle")}</AlertTitle>
              <AlertDescription>
                <p>{boardDenied ? t("denied") : t("loadError")}</p>
                {!boardDenied ? (
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
            {canCheckIn ? <CheckInPanel openRecord={openRecord} /> : null}
          </>
        ) : (
          <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1.65fr)_minmax(20rem,1fr)]">
            <div className="flex flex-col gap-6">
              {canCheckIn ? <CheckInPanel openRecord={openRecord} /> : <ReadOnlyNotice />}
              {boardQuery.isPending ? (
                <BoardCountsPlaceholder
                  note={
                    <span className="flex items-center gap-2" role="status">
                      <Spinner className="size-3.5" aria-hidden="true" />
                      {t("loading")}
                    </span>
                  }
                />
              ) : board ? (
                <BoardCountsStrip counts={board.counts} />
              ) : null}
            </div>

            {board ? (
              <ActivityFeed
                activity={board.activity}
                directory={directory}
                action={
                  <Link
                    href="/command-center"
                    className="flex min-h-11 items-center gap-1 text-xs font-medium text-primary underline-offset-4 hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none sm:min-h-8"
                  >
                    {t("activity.openCommandCenter")}
                    <ArrowRight className="size-3.5" aria-hidden="true" />
                  </Link>
                }
              />
            ) : (
              <ActivityPlaceholder pending={boardQuery.isPending} />
            )}
          </div>
        )
      ) : canCheckIn ? (
        <CheckInPanel openRecord={openRecord} />
      ) : (
        <ReadOnlyNotice />
      )}

      <section className="flex flex-col gap-4">
        <div className="flex flex-col gap-1">
          <h2 className="text-lg leading-6 font-semibold tracking-tight">{t("recordsTitle")}</h2>
          <p className="max-w-2xl text-sm leading-5 text-muted-foreground">
            {t("recordsDescription")}
          </p>
        </div>
        {/* Changing the day remounts the table with that day's date range. */}
        <AttendanceRecordsTable key={date} directory={directory} date={date} />
      </section>

      {canManageShifts ? <ShiftsPanel /> : null}
    </div>
  );
}
