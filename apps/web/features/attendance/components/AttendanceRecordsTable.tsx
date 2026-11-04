"use client";

/**
 * Attendance records table — filters (employee, workday date range, late),
 * chips for mode/late/duration, pagination. Clearing a filter drops the
 * parameter (`undefined`), never `null`: the generated client serializes null
 * as the literal string "null" → the API 422s (employees regression pattern).
 * Client leaf.
 */

import { CircleAlert, ClipboardList } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
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

import { useDebouncedValue } from "@/hooks/use-debounced-value";

import type { EmployeeDirectory } from "@/features/realtime/utils";
import { employeeCode, employeeDisplayName } from "@/features/realtime/utils";

import { useAttendanceRecords } from "../hooks/use-attendance";
import type { ListAttendanceRecordsParams } from "../types";
import { formatClockTime, formatDate, formatDuration } from "./dates";

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
  const t = useTranslations("attendance.records");

  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="attendance-filters">
      <div className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-2 sm:flex">
        <Input
          aria-label={t("fromDate")}
          type="date"
          className="w-full sm:w-40"
          value={params.from ?? ""}
          onChange={(event) => onChange({ from: event.target.value || undefined, page: 1 })}
        />
        <span className="text-sm text-muted-foreground">{t("to")}</span>
        <Input
          aria-label={t("toDate")}
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
        <SelectTrigger aria-label={t("latenessAria")} className="w-full sm:w-36">
          <SelectValue placeholder={t("anyLateness")} />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={LATE_ANY}>{t("anyLateness")}</SelectItem>
          <SelectItem value={LATE_YES}>{t("lateOnly")}</SelectItem>
          <SelectItem value={LATE_NO}>{t("onTimeOnly")}</SelectItem>
        </SelectContent>
      </Select>

      <Input
        aria-label={t("employeeIdAria")}
        placeholder={t("employeeIdPlaceholder")}
        className="w-full sm:w-64"
        value={params.employee_id ?? ""}
        onChange={(event) => onChange({ employee_id: event.target.value || undefined, page: 1 })}
      />
    </div>
  );
}

export function AttendanceRecordsTable({
  directory,
  date,
}: {
  directory?: EmployeeDirectory;
  /** Initial date range, e.g. the attendance board's selected day. */
  date?: string;
} = {}) {
  const t = useTranslations("attendance.records");
  const common = useTranslations("common.actions");
  const workModes = useTranslations("attendance.workModes");
  const units = useTranslations("attendance.units");
  const locale = useLocale();
  const [params, setParams] = useState<ListAttendanceRecordsParams>({
    page: 1,
    page_size: RECORDS_PAGE_SIZE,
    from: date,
    to: date,
  });
  // The employee-id filter box state — fed to the query through
  // useDebouncedValue so typing costs one list request on a 300 ms pause, not
  // one per keystroke. Typing resets the page immediately; only the API call
  // waits. Dates and the lateness select are one-shot: they commit directly.
  const [employeeIdInput, setEmployeeIdInput] = useState("");
  const debouncedEmployeeId = useDebouncedValue(employeeIdInput);

  const recordsQuery = useAttendanceRecords({
    ...params,
    employee_id: debouncedEmployeeId || undefined,
  });
  const listResponse = recordsQuery.data;
  const records = listResponse && listResponse.status === 200 ? listResponse.data.items : [];
  const page = listResponse && listResponse.status === 200 ? listResponse.data.page : null;

  function clearAll() {
    setEmployeeIdInput("");
    // Clear returns to the section's default scope (the board's day), not to
    // every record ever written.
    setParams({ page: 1, page_size: RECORDS_PAGE_SIZE, from: date, to: date });
  }

  function change(next: Partial<ListAttendanceRecordsParams>) {
    // The employee-id input goes through the debounce; dates and selects
    // commit directly.
    if ("employee_id" in next) {
      setEmployeeIdInput(next.employee_id ?? "");
      setParams((current) => (current.page === 1 ? current : { ...current, page: 1 }));
      return;
    }
    setParams((current) => ({ ...current, ...next }));
  }

  return (
    <Card className="gap-0 overflow-hidden rounded-md border-border py-0 shadow-none">
      <div className="flex flex-wrap items-center gap-2 border-b bg-muted/50 px-4 py-3 sm:px-5">
        <FiltersRow params={{ ...params, employee_id: employeeIdInput }} onChange={change} />
        <Button
          variant="ghost"
          size="sm"
          className="ml-auto min-h-11 sm:min-h-7"
          onClick={clearAll}
        >
          {common("clear")}
        </Button>
      </div>

      {recordsQuery.isPending ? (
        <div className="flex items-center justify-center gap-2 px-6 py-10 text-sm text-muted-foreground">
          <Spinner className="size-4" />
          <p>{t("loading")}</p>
        </div>
      ) : recordsQuery.isError ? (
        <div className="p-4">
          <Alert variant="destructive">
            <CircleAlert aria-hidden="true" />
            <AlertDescription>
              <p>{t("loadError")}</p>
              <Button
                variant="outline"
                size="sm"
                className="mt-2"
                onClick={() => void recordsQuery.refetch()}
              >
                {common("retry")}
              </Button>
            </AlertDescription>
          </Alert>
        </div>
      ) : records.length === 0 ? (
        <div className="p-4">
          <EmptyState icon={ClipboardList} message={t("empty")} />
        </div>
      ) : (
        <>
          <p className="px-4 py-2 text-xs text-muted-foreground sm:hidden">{t("swipe")}</p>
          <Table
            aria-label={t("label")}
            containerLabel={t("containerLabel")}
            data-testid="attendance-records"
          >
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead className="pl-3">{t("columns.workday")}</TableHead>
                <TableHead>{t("columns.employee")}</TableHead>
                <TableHead>{t("columns.mode")}</TableHead>
                <TableHead>{t("columns.checkedIn")}</TableHead>
                <TableHead>{t("columns.checkedOut")}</TableHead>
                <TableHead>{t("columns.status")}</TableHead>
                <TableHead className="pr-3 text-right">{t("columns.duration")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {records.map((record) => (
                <TableRow key={record.id}>
                  <TableCell className="pl-3">{formatDate(record.workday, locale)}</TableCell>
                  <TableCell>
                    <div className="flex flex-col">
                      <span className="font-medium">
                        {directory
                          ? employeeDisplayName(
                              record.employee_id,
                              directory,
                              t("employeeShort", { id: record.employee_id.slice(-4) }),
                            )
                          : t("employeeShort", { id: record.employee_id.slice(-4) })}
                      </span>
                      {directory && employeeCode(record.employee_id, directory) ? (
                        <span className="font-mono text-xs text-muted-foreground">
                          {employeeCode(record.employee_id, directory)}
                        </span>
                      ) : null}
                    </div>
                  </TableCell>
                  <TableCell>{workModes(record.work_mode)}</TableCell>
                  <TableCell className="text-sm tabular-nums">
                    <time dateTime={record.checked_in_at} title={record.checked_in_at}>
                      {formatClockTime(record.checked_in_at, locale)}
                    </time>
                  </TableCell>
                  <TableCell className="text-sm tabular-nums">
                    {record.checked_out_at ? (
                      <time dateTime={record.checked_out_at} title={record.checked_out_at}>
                        {formatClockTime(record.checked_out_at, locale)}
                      </time>
                    ) : (
                      "—"
                    )}
                  </TableCell>
                  <TableCell>
                    {record.late ? (
                      <Badge variant="warning" data-status="late">
                        {t("late")}
                      </Badge>
                    ) : (
                      <span className="text-sm text-muted-foreground" data-status="on_time">
                        {t("onTime")}
                      </span>
                    )}
                  </TableCell>
                  <TableCell className="pr-3 text-right text-sm tabular-nums">
                    {record.duration_minutes === null || record.duration_minutes === undefined ? (
                      <Badge variant="outline">{t("open")}</Badge>
                    ) : (
                      formatDuration(
                        record.duration_minutes,
                        units("hourShort"),
                        units("minuteShort"),
                      )
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>

          {page ? (
            <div className="flex flex-wrap items-center justify-between gap-2 border-t bg-muted/50 px-4 py-3 text-sm">
              <span className="text-muted-foreground tabular-nums">
                {t("summary", { total: page.total_items })}
              </span>
              {page.total_pages > 1 ? (
                <div className="flex items-center gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    className="min-h-11 sm:min-h-8"
                    aria-label={t("previousAria")}
                    disabled={page.page <= 1}
                    onClick={() => change({ page: page.page - 1 })}
                  >
                    {t("previous")}
                  </Button>
                  <span className="text-muted-foreground">
                    {t("pageOf", { page: page.page, total: page.total_pages })}
                  </span>
                  <Button
                    variant="outline"
                    size="sm"
                    className="min-h-11 sm:min-h-8"
                    aria-label={t("nextAria")}
                    disabled={page.page >= page.total_pages}
                    onClick={() => change({ page: page.page + 1 })}
                  >
                    {t("next")}
                  </Button>
                </div>
              ) : null}
            </div>
          ) : null}
        </>
      )}
    </Card>
  );
}
