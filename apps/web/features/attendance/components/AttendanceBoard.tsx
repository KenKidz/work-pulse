"use client";

/**
 * The live workforce board — request/response in Phase 1 (polling comes
 * later). Composes: the actor's check-in/out panel, the six count cards, the
 * activity feed and the records table, all keyed on one explicit date.
 * The web always sends `?date=` explicitly (the server default is the UTC
 * date — T1); today is computed client-side via `todayISO()`.
 * Client leaf: queries + mutations coordinate through the Query cache.
 */

import { useState } from "react";

import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

import {
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

  const boardDenied =
    boardQuery.isError &&
    boardQuery.error instanceof ApiError &&
    (boardQuery.error.status === 403 || boardQuery.error.code === "forbidden");

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <label className="text-sm text-muted-foreground" htmlFor="board-date">
            Board date
          </label>
          <Input
            id="board-date"
            type="date"
            className="w-40"
            value={date}
            onChange={(event) => setDate(event.target.value || todayISO())}
          />
        </div>
        <p className="text-xs text-muted-foreground">
          Server time decides every record; this date scopes counts and the feed.
        </p>
      </div>

      <CheckInPanel openRecord={openRecord} />

      {boardQuery.isPending ? (
        <p className="text-sm text-muted-foreground">Loading board…</p>
      ) : boardQuery.isError || boardDenied ? (
        <div role="alert" className="rounded-md border border-destructive/40 p-4 text-sm">
          <p>
            {boardDenied
              ? "The live board is available to managers, HR and admins."
              : "Could not load the attendance board."}
          </p>
          {!boardDenied ? (
            <Button
              variant="outline"
              size="sm"
              className="mt-3"
              onClick={() => void boardQuery.refetch()}
            >
              Retry
            </Button>
          ) : null}
        </div>
      ) : board ? (
        <>
          <BoardCountCards counts={board.counts} />
          <div className="grid gap-6 lg:grid-cols-2">
            <ActivityFeed activity={board.activity} />
            <Card>
              <CardContent className="pt-2 text-sm text-muted-foreground">
                <p className="font-medium text-foreground">How the counts read</p>
                <p className="mt-2">
                  Counts are independent, not a partition: a late check-in moves the person into
                  Present (or Remote) and Late at once; checking out moves them to Checked out and
                  Late stays.
                </p>
                <p className="mt-2">
                  The board is request/response in Phase 1 — refresh or change the date to update;
                  live push arrives with realtime in Phase 2.
                </p>
              </CardContent>
            </Card>
          </div>
        </>
      ) : null}

      <section className="space-y-4">
        <h2 className="text-lg font-semibold tracking-tight">Attendance records</h2>
        <AttendanceRecordsTable />
      </section>

      <ShiftsPanel />
    </div>
  );
}
