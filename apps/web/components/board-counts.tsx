"use client";

/**
 * The shared day-count strip: six independent numbers (a person can be present
 * and late at once), one hairline card, no nested boxes. Used by the dashboard
 * and the attendance board so the same numbers never render two different ways.
 *
 * Values stay ink except the exceptions — late and absent — because a normal
 * state does not need a signal (workpulse-ui §6, N3).
 */

import type { ReactNode } from "react";
import { useTranslations } from "next-intl";
import { cn } from "cn";

export type BoardCountsShape = {
  present: number;
  remote: number;
  late: number;
  absent: number;
  on_leave: number;
  checked_out: number;
};

type CountField = {
  dataKey: keyof BoardCountsShape;
  labelKey: "present" | "remote" | "late" | "absent" | "onLeave" | "checkedOut";
  valueClassName?: string;
  testId: string;
};

/** One source of truth for the strip order, labels, tones and loading layout. */
export const BOARD_COUNT_FIELDS: readonly CountField[] = [
  { dataKey: "present", labelKey: "present", testId: "count-present" },
  { dataKey: "remote", labelKey: "remote", testId: "count-remote" },
  { dataKey: "late", labelKey: "late", valueClassName: "text-warning", testId: "count-late" },
  {
    dataKey: "absent",
    labelKey: "absent",
    valueClassName: "text-destructive",
    testId: "count-absent",
  },
  {
    dataKey: "on_leave",
    labelKey: "onLeave",
    valueClassName: "text-muted-foreground",
    testId: "count-on-leave",
  },
  { dataKey: "checked_out", labelKey: "checkedOut", testId: "count-checked-out" },
];

type CountCellProps = {
  label: string;
  value: number | "—";
  valueClassName?: string;
  testId?: string;
};

function CountCell({ label, value, valueClassName, testId }: CountCellProps) {
  return (
    <div className="min-w-0 bg-card p-4 sm:p-5">
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

/**
 * Counts respond to their own width, not the viewport: the same strip is a
 * six-up band on the dashboard and a three-up block beside the check-in panel
 * on the attendance board. Keep the footer in both states so the loading
 * placeholder cannot shift the layout.
 */
function CountsFrame({ children, note }: { children: ReactNode; note?: ReactNode }) {
  const t = useTranslations("attendance.counts");
  return (
    <section
      aria-label={t("ariaLabel")}
      className="@container/counts overflow-hidden rounded-md border border-border bg-card shadow-none"
    >
      <dl className="grid grid-cols-2 gap-px bg-border @md:grid-cols-3 @4xl:grid-cols-6">
        {children}
      </dl>
      <div className="border-t border-border px-4 py-3 text-xs leading-5 text-muted-foreground sm:px-5">
        <p>{t("overlapHint")}</p>
        <p className="mt-1">
          {t("absentHint")} {t("onLeaveHint")}
        </p>
        {note ? <p className="mt-1">{note}</p> : null}
      </div>
    </section>
  );
}

export function BoardCountsStrip({
  counts,
  note,
}: {
  counts: BoardCountsShape;
  /** Extra line in the footer, e.g. the dashboard's snapshot cadence. */
  note?: ReactNode;
}) {
  const t = useTranslations("attendance.counts");

  return (
    <CountsFrame note={note}>
      {BOARD_COUNT_FIELDS.map((field) => (
        <CountCell
          key={field.dataKey}
          label={t(field.labelKey)}
          value={counts[field.dataKey]}
          valueClassName={field.valueClassName}
          testId={field.testId}
        />
      ))}
    </CountsFrame>
  );
}

/** Stable-height placeholder for the strip's first paint. */
export function BoardCountsPlaceholder({ note }: { note?: ReactNode }) {
  const t = useTranslations("attendance.counts");

  return (
    <CountsFrame note={note}>
      {BOARD_COUNT_FIELDS.map((field) => (
        <CountCell
          key={field.dataKey}
          label={t(field.labelKey)}
          value="—"
          valueClassName="text-muted-foreground"
        />
      ))}
    </CountsFrame>
  );
}
