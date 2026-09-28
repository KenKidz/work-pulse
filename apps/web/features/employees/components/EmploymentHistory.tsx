"use client";

/**
 * Employment history — append-only periods, newest first, the open period
 * marked as current. Names are resolved by the caller so the component stays
 * presentational. Client leaf.
 */

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
      {periods.map((employment) => (
        <li key={employment.id} data-testid="employment-period" className="rounded-md border p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="font-medium">
              {departmentName(employment.department_id)}
              <span className="text-muted-foreground">
                {" "}
                · {positionTitle(employment.position_id)} · {locationName(employment.location_id)}
              </span>
            </p>
            {!employment.effective_to ? (
              <span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
                Current
              </span>
            ) : null}
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            {formatDate(employment.effective_from)} –{" "}
            {employment.effective_to ? formatDate(employment.effective_to) : "Present"}
            {" · "}
            {employmentTypeLabel[employment.employment_type]}
          </p>
        </li>
      ))}
    </ol>
  );
}
