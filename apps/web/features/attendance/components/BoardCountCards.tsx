"use client";

/**
 * Six board count cards — independent counts, not a partition (T1). Copy
 * states the two documented limitations: overlapping `late`×presence and the
 * `absent` over-count on rest days (Decision #7). `on_leave` stays 0 until
 * Phase 3. Client leaf.
 */

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import type { BoardCounts } from "../types";
import { absentHint, onLeaveHint, workModeHint } from "./labels";

type CountCardProps = {
  label: string;
  value: number;
  hint?: string;
  testId?: string;
};

function CountCard({ label, value, hint, testId }: CountCardProps) {
  return (
    <Card className="py-4">
      <CardHeader className="px-4">
        <CardTitle className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
          {label}
        </CardTitle>
      </CardHeader>
      <CardContent className="px-4">
        <p className="text-3xl font-semibold tabular-nums" data-testid={testId}>
          {value}
        </p>
        {hint ? <p className="mt-1 text-xs text-muted-foreground">{hint}</p> : null}
      </CardContent>
    </Card>
  );
}

export function BoardCountCards({ counts }: { counts: BoardCounts }) {
  return (
    <>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <CountCard label="Present" value={counts.present} testId="count-present" />
        <CountCard label="Remote" value={counts.remote} testId="count-remote" />
        <CountCard label="Late" value={counts.late} testId="count-late" />
        <CountCard
          label="On leave"
          value={counts.on_leave}
          hint={onLeaveHint}
          testId="count-on-leave"
        />
        <CountCard label="Absent" value={counts.absent} hint={absentHint} testId="count-absent" />
        <CountCard label="Checked out" value={counts.checked_out} testId="count-checked-out" />
      </div>
      <p className="text-xs text-muted-foreground">{workModeHint}</p>
    </>
  );
}
