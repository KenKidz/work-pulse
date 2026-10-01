"use client";

/**
 * The wall's counts — overlapping, never a partition (ADR 0009): `late` cuts
 * across the presence statuses. Values carry the semantic status tokens and
 * the footer repeats the plain-language caveats. Client leaf.
 */

import { cn } from "cn";

import { absentCountHint, countsOverlapHint, onLeaveCountHint } from "@/lib/attendance-copy";

import type { RealtimeBoardCounts } from "../types";

type CountCellProps = {
  label: string;
  value: number;
  valueClassName: string;
  testId: string;
};

function CountCell({ label, value, valueClassName, testId }: CountCellProps) {
  return (
    <div className="min-w-0">
      <dt className="text-sm font-medium text-muted-foreground">{label}</dt>
      <dd
        className={cn("mt-1.5 text-3xl leading-none font-semibold tabular-nums", valueClassName)}
        data-testid={testId}
      >
        {value}
      </dd>
    </div>
  );
}

export function LiveCounts({ counts }: { counts: RealtimeBoardCounts }) {
  return (
    <section
      aria-label="Presence counts"
      className="rounded-md border border-border bg-card shadow-none"
    >
      <dl className="grid grid-cols-2 gap-x-4 gap-y-5 p-4 sm:grid-cols-3 sm:p-5 lg:grid-cols-6">
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
          testId="count-absent"
        />
        <CountCell
          label="Checked out"
          value={counts.checked_out}
          valueClassName="text-foreground"
          testId="count-checked-out"
        />
        <CountCell
          label="On leave"
          value={counts.on_leave}
          valueClassName="text-muted-foreground"
          testId="count-on-leave"
        />
      </dl>
      <div className="border-t border-border px-4 py-3 text-xs leading-5 text-muted-foreground sm:px-5">
        <p>{countsOverlapHint}</p>
        <p>
          {absentCountHint} {onLeaveCountHint}
        </p>
      </div>
    </section>
  );
}
