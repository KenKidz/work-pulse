"use client";

/**
 * Recent-activity feed — check-in/out entries of the board date, newest first
 * (the API caps at 20). Wire values render through `boardActionLabel`; the
 * employee id is shown as a short code until a roster/name join exists on the
 * board contract. Client leaf.
 */

import { Activity } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { cn } from "cn";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/empty-state";

import type { BoardActivity } from "../types";
import { formatClockTime } from "./dates";

export function ActivityFeed({ activity }: { activity: BoardActivity[] }) {
  const t = useTranslations("attendance.activity");
  const locale = useLocale();

  return (
    <Card data-testid="activity-feed" className="gap-0 rounded-md border-border py-0 shadow-none">
      <CardHeader className="px-5 pt-5 pb-4">
        <CardTitle>{t("title")}</CardTitle>
        <CardDescription>{t("description")}</CardDescription>
      </CardHeader>
      <CardContent className="px-5 pb-5">
        {activity.length === 0 ? (
          <EmptyState icon={Activity} message={t("empty")} />
        ) : (
          <ol
            aria-label={t("ariaLabel")}
            className="relative flex flex-col gap-1 before:absolute before:top-4 before:bottom-4 before:left-[5.25rem] before:w-px before:bg-border before:content-['']"
          >
            {activity.map((entry) => {
              const checkedIn = entry.action === "checked_in";
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
                    {employeeShort(entry.employee_id, t)} {t(entry.action)}
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

/** `01900000-…a001` → `Employee …a001` — no name join on the board contract. */
function employeeShort(
  id: string,
  t: (key: "employeeShort", values: { id: string }) => string,
): string {
  return t("employeeShort", { id: id.slice(-4) });
}
