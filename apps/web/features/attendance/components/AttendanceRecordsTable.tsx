"use client";

/**
 * Attendance records table — filters (employee, workday date range, late),
 * chips for mode/late/duration, pagination. Clearing a filter drops the
 * parameter (`undefined`), never `null`: the generated client serializes null
 * as the literal string "null" → the API 422s (employees regression pattern).
 * Client leaf.
 */

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
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
import { formatDate, formatDuration } from "./dates";
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
      <div className="flex items-center gap-2">
        <Input
          aria-label="From date"
          type="date"
          className="w-40"
          value={params.from ?? ""}
          onChange={(event) => onChange({ from: event.target.value || undefined, page: 1 })}
        />
        <span className="text-sm text-muted-foreground">to</span>
        <Input
          aria-label="To date"
          type="date"
          className="w-40"
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
        <SelectTrigger aria-label="Filter by lateness" className="w-36">
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
        className="w-64"
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
    <div className="space-y-4">
      <FiltersRow params={params} onChange={change} />

      {recordsQuery.isPending ? (
        <p className="text-sm text-muted-foreground">Loading attendance records…</p>
      ) : recordsQuery.isError ? (
        <div role="alert" className="rounded-md border border-destructive/40 p-4 text-sm">
          <p>Could not load attendance records.</p>
          <Button
            variant="outline"
            size="sm"
            className="mt-3"
            onClick={() => void recordsQuery.refetch()}
          >
            Retry
          </Button>
        </div>
      ) : records.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No attendance records match the filters yet.
        </p>
      ) : (
        <>
          <Table data-testid="attendance-records">
            <TableHeader>
              <TableRow>
                <TableHead>Workday</TableHead>
                <TableHead>Employee</TableHead>
                <TableHead>Mode</TableHead>
                <TableHead>Checked in</TableHead>
                <TableHead>Checked out</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Duration</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {records.map((record) => (
                <TableRow key={record.id}>
                  <TableCell>{formatDate(record.workday)}</TableCell>
                  <TableCell className="font-mono text-xs">
                    emp {record.employee_id.slice(-4)}
                  </TableCell>
                  <TableCell>{workModeLabel[record.work_mode]}</TableCell>
                  <TableCell className="font-mono text-xs">{record.checked_in_at}</TableCell>
                  <TableCell className="font-mono text-xs">
                    {record.checked_out_at ?? "—"}
                  </TableCell>
                  <TableCell>
                    {record.late ? (
                      <span
                        data-status="late"
                        className="rounded-full border border-destructive/40 px-2 py-0.5 text-xs text-destructive"
                      >
                        Late
                      </span>
                    ) : (
                      <span
                        data-status="on_time"
                        className="rounded-full border px-2 py-0.5 text-xs"
                      >
                        On time
                      </span>
                    )}
                  </TableCell>
                  <TableCell className="text-right font-mono text-xs tabular-nums">
                    {record.duration_minutes === null || record.duration_minutes === undefined
                      ? "Open"
                      : formatDuration(record.duration_minutes)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>

          {page && page.total_pages > 1 ? (
            <div className="flex items-center justify-end gap-2 text-sm">
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
    </div>
  );
}
