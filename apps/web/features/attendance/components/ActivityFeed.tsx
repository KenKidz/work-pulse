"use client";

/**
 * Recent-activity feed — check-in/out entries of the board date, newest first
 * (the API caps at 20). Wire values render through `boardActionLabel`; the
 * employee id is shown as a short code until a roster/name join exists on the
 * board contract. Client leaf.
 */

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

import type { BoardActivity } from "../types";
import { formatClockTime } from "./dates";
import { boardActionLabel } from "./labels";

export function ActivityFeed({ activity }: { activity: BoardActivity[] }) {
  return (
    <Card data-testid="activity-feed">
      <CardHeader>
        <CardTitle>Recent activity</CardTitle>
        <CardDescription>Check-ins and check-outs for the board date.</CardDescription>
      </CardHeader>
      <CardContent>
        {activity.length === 0 ? (
          <p className="text-sm text-muted-foreground">No activity yet for this date.</p>
        ) : (
          <ul className="space-y-2">
            {activity.map((entry) => (
              <li
                key={`${entry.employee_id}-${entry.action}-${entry.occurred_at}`}
                className="flex items-center gap-2 text-sm"
              >
                <span className="font-mono text-xs tabular-nums">
                  {formatClockTime(entry.occurred_at)}
                </span>
                <span>
                  {employeeShort(entry.employee_id)} {boardActionLabel[entry.action]}
                </span>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

/** `01900000-…a001` → `…a001` — short, stable, no name join on the contract. */
function employeeShort(id: string): string {
  return `emp ${id.slice(-4)}`;
}
