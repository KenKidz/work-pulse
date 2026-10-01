"use client";

/**
 * Live activity rail — check-ins and check-outs pushed by `presence.upsert`
 * frames (plus the snapshot's recent history), newest first, capped at 20.
 * Names are joined client-side. An `sr-only` polite status echoes the newest
 * entry, so arrivals are announced without re-reading the whole list. Client
 * leaf.
 *
 * Guide-rail geometry: rows grid as `3rem` time column + `gap-x-3` + a `0.75rem`
 * dot column, so the dot column's center sits at 4.125rem (3rem + 0.75rem +
 * half of 0.75rem) — the rail's `before` element anchors there, not on the
 * column's left edge, and the card-halo dots sit on top of the 1px line.
 */

import { Activity } from "lucide-react";
import { cn } from "cn";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/empty-state";

import { activityActionLabel } from "../labels";
import type { RealtimeActivityEntry } from "../types";
import type { EmployeeDirectory } from "../utils";
import { employeeDisplayName, formatClockTime } from "../utils";

export function LiveActivityRail({
  activity,
  directory,
}: {
  activity: RealtimeActivityEntry[];
  directory: EmployeeDirectory;
}) {
  const latest = activity.at(0);
  return (
    <Card data-testid="live-activity" className="gap-0 rounded-md border-border py-0 shadow-none">
      <CardHeader className="px-5 pt-5 pb-4">
        <CardTitle>Live activity</CardTitle>
        <CardDescription>Check-ins and check-outs, newest first.</CardDescription>
      </CardHeader>
      <CardContent className="px-5 pb-5">
        {/* One concise sentence per event — the visible list is a snapshot of
            the day, not a status message. */}
        <p
          role="status"
          aria-atomic="true"
          data-testid="live-activity-announcer"
          className="sr-only"
        >
          {latest === undefined
            ? ""
            : `${employeeDisplayName(latest.employee_id, directory)} ${activityActionLabel[latest.action]} at ${formatClockTime(latest.occurred_at)}`}
        </p>
        {activity.length === 0 ? (
          <EmptyState
            icon={Activity}
            message="Nothing yet today — the rail fills as people check in and out."
          />
        ) : (
          <ol
            aria-label="Live activity"
            className="relative flex flex-col gap-1 before:absolute before:inset-y-4 before:left-[4.125rem] before:w-px before:bg-border before:content-['']"
          >
            {activity.map((entry) => {
              const checkedIn = entry.action === "checked_in";
              return (
                <li
                  key={`${entry.employee_id}-${entry.action}-${entry.occurred_at}`}
                  className="grid grid-cols-[3rem_0.75rem_minmax(0,1fr)] items-center gap-x-3 py-2"
                >
                  <time
                    dateTime={entry.occurred_at}
                    className="text-right font-mono text-xs text-muted-foreground tabular-nums"
                  >
                    {formatClockTime(entry.occurred_at)}
                  </time>
                  <span
                    aria-hidden="true"
                    className="relative z-10 mx-auto flex size-3 items-center justify-center rounded-full bg-card"
                  >
                    <span
                      className={cn("size-2 rounded-full", checkedIn ? "bg-success" : "bg-primary")}
                    />
                  </span>
                  <span className="min-w-0 text-sm break-words">
                    <span className="font-medium">
                      {employeeDisplayName(entry.employee_id, directory)}
                    </span>{" "}
                    <span className="text-muted-foreground">
                      {activityActionLabel[entry.action]}
                    </span>
                  </span>
                </li>
              );
            })}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}
