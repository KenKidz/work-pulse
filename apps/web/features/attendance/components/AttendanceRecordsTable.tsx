"use client";

/**
 * Attendance records table — filters (employee, workday date range, late),
 * chips for mode/late/duration, pagination. Clearing a filter drops the
 * parameter (`undefined`), never `null`: the generated client serializes null
 * as the literal string "null" → the API 422s (employees regression pattern).
 * Client leaf.
 */

import { CircleAlert, ClipboardList } from "lucide-react";
import { useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/empty-state";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

import { useAttendanceRecords } from "../hooks/use-attendance";
import type { ListAttendanceRecordsParams } from "../types";
import { formatClockTime, formatDate, formatDuration } from "./dates";
import { workModeLabel } from "./labels";

export const ANY = "any";

export const RECORDS_PAGE_SIZE = 20;

const LATE_ANY = ANY;
const LATE_YES = "yes";
const LATE_NO = "no";

function FiltersRow({
  params,
  onChange,
}: {
  params: ListAttendanceRecordsParams;
  onChange: (next: Partial<ListAttendanceRecordsParams>) => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="attendance-filters">
      <div className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-2 sm:flex">
        <Input
          aria-label="From date"
          type="date"
          className="w-full sm:w-40"
          value={params.from ?? ""}
          onChange={(event) => onChange({ from: event.target.value || undefined, page: 1 })}
        />
        <span className="text-sm text-muted-foreground">to</span>
        <Input
          aria-label="To date"
          type="date"
          className="w-full sm:w-40"
          value={params.to ?? ""}
          onChange={(event) => onChange({ to: event.target.value || undefined, page: 1 })}
        />
      </div>

      <Select
        value={
          params.late === undefined || params.late === null
            ? LATE_ANY
            : params.late
              ? LATE_YES
              : LATE_NO
        }
        onValueChange={(value) =>
          onChange({
            late: value === LATE_ANY ? undefined : value === LATE_YES,
            page: 1,
          })
        }
      >
        <SelectTrigger aria-label="Filter by lateness" className="w-full sm:w-36">
          <SelectValue placeholder="Any lateness" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={LATE_ANY}>Any lateness</SelectItem>
          <SelectItem value={LATE_YES}>Late only</SelectItem>
          <SelectItem value={LATE_NO}>On time only</SelectItem>
        </SelectContent>
      </Select>

      <Input
        aria-label="Filter by employee id"
        placeholder="Employee id"
        className="w-full sm:w-64"
        value={params.employee_id ?? ""}
        onChange={(event) => onChange({ employee_id: event.target.value || undefined, page: 1 })}
      />
    </div>
  );
}

export function AttendanceRecordsTable() {
  const [params, setParams] = useState<ListAttendanceRecordsParams>({
    page: 1,
    page_size: RECORDS_PAGE_SIZE,
  });

  const recordsQuery = useAttendanceRecords(params);
  const listResponse = recordsQuery.data;
  const records = listResponse && listResponse.status === 200 ? listResponse.data.items : [];
  const page = listResponse && listResponse.status === 200 ? listResponse.data.page : null;

  function change(next: Partial<ListAttendanceRecordsParams>) {
    setParams((current) => ({ ...current, ...next }));
  }

  return (
    <Card className="gap-0 overflow-hidden rounded-md border-border py-0 shadow-none">
      <div className="border-b bg-muted/50 px-4 py-3 sm:px-5">
        <FiltersRow params={params} onChange={change} />
      </div>

      {recordsQuery.isPending ? (
        <div className="flex items-center justify-center gap-2 px-6 py-10 text-sm text-muted-foreground">
          <Spinner className="size-4" />
          <p>Loading attendance records…</p>
        </div>
      ) : recordsQuery.isError ? (
        <div className="p-4">
          <Alert variant="destructive">
            <CircleAlert aria-hidden="true" />
            <AlertDescription>
              <p>Could not load attendance records.</p>
              <Button
                variant="outline"
                size="sm"
                className="mt-2"
                onClick={() => void recordsQuery.refetch()}
              >
                Retry
              </Button>
            </AlertDescription>
          </Alert>
        </div>
      ) : records.length === 0 ? (
        <div className="p-4">
          <EmptyState icon={ClipboardList} message="No attendance records match the filters yet." />
        </div>
      ) : (
        <>
          <p className="px-4 py-2 text-xs text-muted-foreground sm:hidden">
            Swipe sideways to see every attendance field.
          </p>
          <Table
            aria-label="Attendance records"
            containerLabel="Attendance records. Scroll horizontally to read all columns."
            data-testid="attendance-records"
          >
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead className="pl-3">Workday</TableHead>
                <TableHead>Employee</TableHead>
                <TableHead>Mode</TableHead>
                <TableHead>Checked in</TableHead>
                <TableHead>Checked out</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="pr-3 text-right">Duration</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {records.map((record) => (
                <TableRow key={record.id}>
                  <TableCell className="pl-3">{formatDate(record.workday)}</TableCell>
                  <TableCell className="text-sm text-muted-foreground">
                    Employee …{record.employee_id.slice(-4)}
                  </TableCell>
                  <TableCell>{workModeLabel[record.work_mode]}</TableCell>
                  <TableCell className="text-sm tabular-nums">
                    <time dateTime={record.checked_in_at} title={record.checked_in_at}>
                      {formatClockTime(record.checked_in_at)}
                    </time>
                  </TableCell>
                  <TableCell className="text-sm tabular-nums">
                    {record.checked_out_at ? (
                      <time dateTime={record.checked_out_at} title={record.checked_out_at}>
                        {formatClockTime(record.checked_out_at)}
                      </time>
                    ) : (
                      "—"
                    )}
                  </TableCell>
                  <TableCell>
                    <Badge
                      variant={record.late ? "warning" : "muted"}
                      data-status={record.late ? "late" : "on_time"}
                    >
                      {record.late ? "Late" : "On time"}
                    </Badge>
                  </TableCell>
                  <TableCell className="pr-3 text-right text-sm tabular-nums">
                    {record.duration_minutes === null || record.duration_minutes === undefined ? (
                      <Badge variant="outline">Open</Badge>
                    ) : (
                      formatDuration(record.duration_minutes)
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>

          {page && page.total_pages > 1 ? (
            <div className="flex items-center justify-end gap-2 border-t px-4 py-3 text-sm">
              <Button
                variant="outline"
                size="sm"
                aria-label="Previous page"
                disabled={page.page <= 1}
                onClick={() => change({ page: page.page - 1 })}
              >
                Previous
              </Button>
              <span className="text-muted-foreground">
                Page {page.page} of {page.total_pages}
              </span>
              <Button
                variant="outline"
                size="sm"
                aria-label="Next page"
                disabled={page.page >= page.total_pages}
                onClick={() => change({ page: page.page + 1 })}
              >
                Next
              </Button>
            </div>
          ) : null}
        </>
      )}
    </Card>
  );
}
