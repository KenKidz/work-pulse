"use client";

/**
 * The attendance board — check-in/out, grouped counts, the activity feed and
 * the records table, all keyed on one explicit date.
 * The web always sends `?date=` explicitly (the server default is the UTC
 * date — T1); today is computed client-side via `todayISO()`.
 * Client leaf: queries + mutations coordinate through the Query cache.
 */

import { CircleAlert } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";

import {
  isForbiddenError,
  useAttendanceBoard,
  useAttendanceRecords,
  useCurrentEmployee,
} from "../hooks/use-attendance";
import type { AttendanceRecord } from "../types";
import { ActivityFeed } from "./ActivityFeed";
import { AttendanceRecordsTable } from "./AttendanceRecordsTable";
import { BoardCountCards } from "./BoardCountCards";
import { CheckInPanel } from "./CheckInPanel";
import { ShiftsPanel } from "./ShiftsPanel";
import { todayISO } from "./dates";

export function AttendanceBoard() {
  const t = useTranslations("attendance");
  const common = useTranslations("common.actions");
  // The board date is explicit — never rely on the server's UTC default.
  const [date, setDate] = useState(todayISO());
  const boardParams = { date };

  const boardQuery = useAttendanceBoard(boardParams);
  const recordsQuery = useAttendanceRecords({ from: date, to: date, page_size: 200 });
  const meQuery = useCurrentEmployee();

  const boardResponse = boardQuery.data;
  const board = boardResponse && boardResponse.status === 200 ? boardResponse.data : null;

  const meResponse = meQuery.data;
  const me = meResponse && meResponse.status === 200 ? meResponse.data : null;
  const meId = me?.id ?? null;

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
      <header className="grid gap-5 border-b border-border pb-6 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end">
        <div className="space-y-2">
          <h1 className="text-3xl leading-9 font-semibold tracking-tight text-balance">
            {t("title")}
          </h1>
          <p className="max-w-2xl text-sm leading-5 text-pretty text-muted-foreground">
            {t("description")}
          </p>
          <p
            id="board-date-description"
            className="max-w-xl pt-1 text-xs leading-5 text-muted-foreground"
          >
            {t("boardDateHint")}
          </p>
        </div>
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
      </header>

      <CheckInPanel openRecord={openRecord} />

      {boardQuery.isPending ? (
        <div className="flex items-center justify-center gap-2 rounded-md border border-dashed px-6 py-10 text-sm text-muted-foreground">
          <Spinner className="size-4" />
          <p>{t("loading")}</p>
        </div>
      ) : boardQuery.isError || boardDenied ? (
        <Alert variant="destructive">
          <CircleAlert aria-hidden="true" />
          <AlertTitle>
            {boardDenied ? t("deniedTitle") : t("loadErrorTitle")}
          </AlertTitle>
          <AlertDescription>
            <p>
              {boardDenied ? t("denied") : t("loadError")}
            </p>
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
      ) : board ? (
        <>
          <BoardCountCards counts={board.counts} />
          <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1.7fr)_minmax(16rem,0.8fr)]">
            <ActivityFeed activity={board.activity} />
            <aside
              aria-labelledby="attendance-refresh-title"
              className="flex flex-col gap-2 border-l-2 border-primary/30 py-1 pl-5"
            >
              <h2 id="attendance-refresh-title" className="text-base font-semibold">
                {t("refreshTitle")}
              </h2>
              <p className="text-sm leading-6 text-muted-foreground">
                {t("refreshBody")}
              </p>
            </aside>
          </div>
        </>
      ) : null}

      <section className="flex flex-col gap-4">
        <h2 className="text-lg leading-6 font-semibold tracking-tight">{t("recordsTitle")}</h2>
        <AttendanceRecordsTable />
      </section>

      <ShiftsPanel />
    </div>
  );
}
