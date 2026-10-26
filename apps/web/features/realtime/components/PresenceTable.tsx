"use client";

/**
 * Who-is-working-now — every row the server sent (records for the workday
 * plus currently-absent employees), already sorted working-first. Display
 * names and codes are joined client-side from the employee directory;
 * `since` renders local clock time via dayjs. Client leaf.
 *
 * Phones get stacked presence rows (name + `since`, then status/late badges
 * and the code) so every field stays visible; `sm+` keeps the table.
 */

import { Users } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/empty-state";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

import { presenceStatusVariant } from "../labels";
import type { PresenceRow, PresenceRowStatus } from "../types";
import type { EmployeeDirectory } from "../utils";
import { employeeCode, employeeDisplayName, formatClockTime, formatMinutes } from "../utils";

function StatusBadge({ status }: { status: PresenceRowStatus }) {
  const t = useTranslations("realtime.status");
  return <Badge variant={presenceStatusVariant[status]}>{t(status)}</Badge>;
}

function LateBadge({ row }: { row: PresenceRow }) {
  const t = useTranslations("realtime.presence");
  const units = useTranslations("realtime.units");
  return (
    <Badge variant="warning">
      {row.minutes_late != null
        ? t("lateMinutes", {
          minutes: formatMinutes(row.minutes_late, units("hourShort"), units("minuteShort")),
        })
        : t("late")}
    </Badge>
  );
}

function SinceText({ since }: { since: string | null | undefined }) {
  const locale = useLocale();
  if (!since) {
    return <span className="font-mono text-xs text-muted-foreground">—</span>;
  }
  return (
    <time dateTime={since} className="font-mono text-xs text-muted-foreground tabular-nums">
      {formatClockTime(since, locale)}
    </time>
  );
}

export function PresenceTable({
  rows,
  directory,
}: {
  rows: PresenceRow[];
  directory: EmployeeDirectory;
}) {
  const t = useTranslations("realtime.presence");
  const root = useTranslations("realtime");
  const locale = useLocale();

  return (
    <Card data-testid="presence-table" className="gap-0 rounded-md border-border py-0 shadow-none">
      <CardHeader className="px-5 pt-5 pb-4">
        <CardTitle>{t("title")}</CardTitle>
        <CardDescription>{t("description")}</CardDescription>
      </CardHeader>

      {rows.length === 0 ? (
        <CardContent className="px-5 pb-5">
          <EmptyState
            icon={Users}
            message={t("empty")}
          />
        </CardContent>
      ) : (
        <>
          {/* Bounded panes: a whole-tenant wall can carry hundreds of rows, so
              both views scroll inside a fixed height instead of turning the
              page into one long strip — the table header sticks and the
              activity rail stays within reach. Stacked rows on phones are the
              wall's glance surface, no sideways scroll. */}
          <div
            role="region"
            aria-label={t("regionLabel")}
            tabIndex={0}
            data-testid="presence-table-mobile-scroll"
            className="max-h-[min(60dvh,36rem)] overflow-y-auto focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring sm:hidden"
          >
            <ul aria-label={t("listLabel")} data-testid="presence-table-mobile" className="flex flex-col">
              {rows.map((row) => {
                const code = employeeCode(row.employee_id, directory);
                return (
                  <li
                    key={row.employee_id}
                    className="border-b border-border px-5 py-3 last:border-b-0"
                  >
                    <div className="flex items-baseline justify-between gap-x-3">
                      <span className="min-w-0 font-medium break-words">
                        {employeeDisplayName(
                          row.employee_id,
                          directory,
                          root("employeeFallback", { id: row.employee_id.slice(-4) }),
                        )}
                      </span>
                      <SinceText since={row.since} />
                    </div>
                    <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1">
                      <StatusBadge status={row.status} />
                      {row.late ? <LateBadge row={row} /> : null}
                      {code ? (
                        <span className="ml-auto font-mono text-xs text-muted-foreground">
                          {code}
                        </span>
                      ) : null}
                    </div>
                  </li>
                );
              })}
            </ul>
          </div>

          <div className="hidden sm:block" data-testid="presence-table-desktop">
            <Table
              aria-label={t("listLabel")}
              containerLabel={t("containerLabel")}
              containerClassName="max-h-[min(60dvh,36rem)] overflow-y-auto"
            >
              <TableHeader sticky>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="pl-5">{t("columns.employee")}</TableHead>
                  <TableHead>{t("columns.status")}</TableHead>
                  <TableHead>{t("columns.late")}</TableHead>
                  <TableHead className="pr-5">{t("columns.since")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((row) => {
                  const code = employeeCode(row.employee_id, directory);
                  return (
                    <TableRow key={row.employee_id} data-testid={`presence-${row.status}`}>
                      <TableCell className="pl-5">
                        <div className="flex flex-col">
                          <span className="font-medium">
                            {employeeDisplayName(
                          row.employee_id,
                          directory,
                          root("employeeFallback", { id: row.employee_id.slice(-4) }),
                        )}
                          </span>
                          {code ? (
                            <span className="font-mono text-xs text-muted-foreground">{code}</span>
                          ) : null}
                        </div>
                      </TableCell>
                      <TableCell>
                        <StatusBadge status={row.status} />
                      </TableCell>
                      <TableCell>
                        {row.late ? (
                          <LateBadge row={row} />
                        ) : (
                          <span className="text-sm text-muted-foreground">—</span>
                        )}
                      </TableCell>
                      <TableCell className="pr-5 font-mono text-xs text-muted-foreground tabular-nums">
                        {row.since ? formatClockTime(row.since, locale) : "—"}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        </>
      )}
    </Card>
  );
}
