"use client";

/**
 * Attendance counts grouped by what they describe: open presence now and
 * independent markers across the selected workday. Client leaf.
 */

import { cn } from "cn";

import type { BoardCounts } from "../types";
import { absentHint, onLeaveHint, workModeHint } from "./labels";

type CountCellProps = {
  label: string;
  value: number;
  valueClassName?: string;
  hint?: string;
  testId: string;
};

function CountCell({ label, value, valueClassName, hint, testId }: CountCellProps) {
  return (
    <div className="min-w-0">
      <dt className="text-sm font-medium text-muted-foreground">{label}</dt>
      <dd
        className={cn("mt-1 text-2xl leading-none font-semibold tabular-nums", valueClassName)}
        data-testid={testId}
      >
        {value}
      </dd>
      {hint ? <p className="mt-2 text-xs leading-4 text-muted-foreground">{hint}</p> : null}
    </div>
  );
}

export function BoardCountCards({ counts }: { counts: BoardCounts }) {
  return (
    <section
      aria-label="Attendance counts"
      className="overflow-hidden rounded-md border border-border bg-card shadow-none"
    >
      <div className="grid md:grid-cols-[minmax(0,4fr)_minmax(0,8fr)]">
        <div className="border-b border-border bg-primary/5 p-4 sm:p-5 md:border-r md:border-b-0">
          <h2 className="text-xs font-semibold text-muted-foreground">Current presence</h2>
          <dl className="mt-4 grid grid-cols-2 gap-4">
            <CountCell
              label="On site"
              value={counts.present}
              valueClassName="text-success"
              testId="count-present"
            />
            <CountCell
              label="Remote"
              value={counts.remote}
              valueClassName="text-info"
              testId="count-remote"
            />
          </dl>
        </div>

        <div className="p-4 sm:p-5">
          <h2 className="text-xs font-semibold text-muted-foreground">Other day counts</h2>
          <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-5 md:grid-cols-4">
            <CountCell
              label="Late"
              value={counts.late}
              valueClassName="text-warning"
              testId="count-late"
            />
            <CountCell
              label="Absent"
              value={counts.absent}
              valueClassName="text-destructive"
              hint={absentHint}
              testId="count-absent"
            />
            <CountCell
              label="On leave"
              value={counts.on_leave}
              valueClassName="text-muted-foreground"
              hint={onLeaveHint}
              testId="count-on-leave"
            />
            <CountCell
              label="Checked out"
              value={counts.checked_out}
              valueClassName="text-foreground"
              testId="count-checked-out"
            />
          </dl>
        </div>
      </div>

      <p className="border-t border-border px-4 py-3 text-xs leading-5 text-muted-foreground sm:px-5">
        {workModeHint}
      </p>
    </section>
  );
}
