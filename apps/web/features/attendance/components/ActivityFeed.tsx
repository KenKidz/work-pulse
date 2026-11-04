"use client";

/**
 * Recent-activity feed — check-in/out entries for the visible day, newest
 * first. The board contract carries identifiers only, so names are joined
 * client-side from the employee directory when the caller has one; otherwise
 * the row falls back to the short id. Bounded on live surfaces so a check-in
 * burst cannot grow the page. Client leaf.
 */

import { Activity } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { cn } from "cn";
import type { ReactNode } from "react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/empty-state";
import type { EmployeeDirectory } from "@/features/realtime/utils";
import { employeeDisplayName } from "@/features/realtime/utils";

import type { BoardActivity } from "../types";
import { formatClockTime } from "./dates";

export function ActivityFeed({
  activity,
  directory,
  limit,
  action,
}: {
  activity: BoardActivity[];
  /** When present, names replace the `Employee …id` fallback. */
  directory?: EmployeeDirectory;
  /** Cap the visible rows; the API already caps the payload at 20. */
  limit?: number;
  /** Optional footer action (the dashboard links to the command center). */
  action?: ReactNode;
}) {
  const t = useTranslations("attendance.activity");
  const locale = useLocale();
  const entries = limit === undefined ? activity : activity.slice(0, limit);
  // Uncapped feeds scroll inside the card instead of growing the page one
  // check-in at a time (workpulse-ui §8); capped feeds are already short.
  const bounded = limit === undefined;

  return (
    <Card data-testid="activity-feed" className="gap-0 rounded-md border-border py-0 shadow-none">
      <CardHeader className="px-5 pt-5 pb-4">
        <CardTitle>{t("title")}</CardTitle>
        <CardDescription>{t("description")}</CardDescription>
      </CardHeader>
      <CardContent className="px-5 pb-5">
        {entries.length === 0 ? (
          <EmptyState icon={Activity} message={t("empty")} />
        ) : (
          <ol
            aria-label={t("ariaLabel")}
            tabIndex={bounded ? 0 : undefined}
            className={cn(
              "relative flex flex-col gap-1 before:absolute before:top-4 before:bottom-4 before:left-[5.25rem] before:w-px before:bg-border before:content-['']",
              bounded &&
                "max-h-[min(60dvh,36rem)] overflow-y-auto overscroll-contain pr-1 focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none",
            )}
          >
            {entries.map((entry) => {
              const checkedIn = entry.action === "checked_in";
              const name =
                directory === undefined
                  ? t("employeeShort", { id: entry.employee_id.slice(-4) })
                  : employeeDisplayName(
                      entry.employee_id,
                      directory,
                      t("employeeShort", { id: entry.employee_id.slice(-4) }),
                    );
              return (
                <li
                  key={`${entry.employee_id}-${entry.action}-${entry.occurred_at}`}
                  className="grid grid-cols-[4rem_1rem_minmax(0,1fr)] items-center gap-x-3 py-2"
                >
                  <time
                    dateTime={entry.occurred_at}
                    className="text-right text-xs font-medium text-muted-foreground tabular-nums"
                  >
                    {formatClockTime(entry.occurred_at, locale)}
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
                    <span className="font-medium">{name}</span> {t(entry.action)}
                  </span>
                </li>
              );
            })}
          </ol>
        )}
      </CardContent>
      {action ? <div className="border-t border-border px-5 py-2.5">{action}</div> : null}
    </Card>
  );
}
