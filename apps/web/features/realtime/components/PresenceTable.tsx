"use client";

/**
 * Who-is-working-now table — every row the server sent (records for the
 * workday plus currently-absent employees), already sorted working-first.
 * Display names and codes are joined client-side from the employee
 * directory; `since` renders local clock time via dayjs. Client leaf.
 */

import { Users } from "lucide-react";

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

import { presenceStatusLabel, presenceStatusVariant } from "../labels";
import type { PresenceRow } from "../types";
import type { EmployeeDirectory } from "../utils";
import { employeeCode, employeeDisplayName, formatClockTime, formatMinutes } from "../utils";

export function PresenceTable({
  rows,
  directory,
}: {
  rows: PresenceRow[];
  directory: EmployeeDirectory;
}) {
  return (
    <Card data-testid="presence-table" className="gap-0 rounded-md border-border py-0 shadow-none">
      <CardHeader className="px-5 pt-5 pb-4">
        <CardTitle>Who’s working now</CardTitle>
        <CardDescription>
          Today’s presence for everyone in your scope — rows update as check-ins happen.
        </CardDescription>
      </CardHeader>

      {rows.length === 0 ? (
        <CardContent className="px-5 pb-5">
          <EmptyState
            icon={Users}
            message="No one is on the board yet today — check-ins appear here the moment they happen."
          />
        </CardContent>
      ) : (
        <>
          <p className="px-4 pb-2 text-xs text-muted-foreground sm:hidden">
            Swipe sideways to see every presence field.
          </p>
          <Table
            aria-label="Presence"
            containerLabel="Presence. Scroll horizontally to read all columns."
          >
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead className="pl-5">Employee</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Late</TableHead>
                <TableHead className="pr-5">Since</TableHead>
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
                          {employeeDisplayName(row.employee_id, directory)}
                        </span>
                        {code ? (
                          <span className="font-mono text-xs text-muted-foreground">{code}</span>
                        ) : null}
                      </div>
                    </TableCell>
                    <TableCell>
                      <Badge variant={presenceStatusVariant[row.status]}>
                        {presenceStatusLabel[row.status]}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      {row.late ? (
                        <Badge variant="warning">
                          {row.minutes_late != null
                            ? `Late · ${formatMinutes(row.minutes_late)}`
                            : "Late"}
                        </Badge>
                      ) : (
                        <span className="text-sm text-muted-foreground">—</span>
                      )}
                    </TableCell>
                    <TableCell className="pr-5 font-mono text-xs text-muted-foreground tabular-nums">
                      {row.since ? formatClockTime(row.since) : "—"}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </>
      )}
    </Card>
  );
}
