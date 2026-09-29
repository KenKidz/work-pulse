"use client";

/**
 * Employment history — append-only periods, newest first, the open period
 * marked as current. Names are resolved by the caller so the component stays
 * presentational. Client leaf.
 */

import { cn } from "cn";

import { Badge } from "@/components/ui/badge";

import { employmentTypeLabel } from "./labels";
import { formatDate } from "./dates";
import type { Employment } from "../types";

type EmploymentHistoryProps = {
  employments: Employment[];
  departmentName: (id: string) => string;
  positionTitle: (id: string) => string;
  locationName: (id: string) => string;
};

export function EmploymentHistory({
  employments,
  departmentName,
  positionTitle,
  locationName,
}: EmploymentHistoryProps) {
  // The API already returns newest first; sort defensively so a regression
  // cannot show the wrong "current" period.
  const periods = [...employments].sort((a, b) => b.effective_from.localeCompare(a.effective_from));

  if (periods.length === 0) {
    return <p className="text-sm text-muted-foreground">No employment periods recorded.</p>;
  }

  return (
    <ol className="space-y-3">
      {periods.map((employment) => {
        const current = !employment.effective_to;
        return (
          <li
            key={employment.id}
            data-testid="employment-period"
            className="relative overflow-hidden rounded-lg border bg-card p-3 pl-4"
          >
            <span
              aria-hidden="true"
              className={cn("absolute inset-y-0 left-0 w-1", current ? "bg-primary" : "bg-border")}
            />
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="font-medium">
                {departmentName(employment.department_id)}
                <span className="text-muted-foreground">
                  {" "}
                  · {positionTitle(employment.position_id)} · {locationName(employment.location_id)}
                </span>
              </p>
              {current ? <Badge variant="success">Current</Badge> : null}
            </div>
            <p className="mt-1 text-sm text-muted-foreground">
              {formatDate(employment.effective_from)} –{" "}
              {employment.effective_to ? formatDate(employment.effective_to) : "Present"}
              {" · "}
              {employmentTypeLabel[employment.employment_type]}
            </p>
          </li>
        );
      })}
    </ol>
  );
}
