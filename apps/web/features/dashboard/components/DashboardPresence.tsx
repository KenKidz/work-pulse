"use client";

/**
 * Compact "who is working now" card for the dashboard. Reuses the command
 * center's directory join and status tones, but shows a short, unbounded-list
 * slice instead of the wall's full scrolled table. Client leaf.
 */

import { Users } from "lucide-react";
import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";

import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/empty-state";
import { presenceStatusVariant } from "@/features/realtime/labels";
import type { PresenceRow } from "@/features/realtime/types";
import type { EmployeeDirectory } from "@/features/realtime/utils";
import { employeeCode, employeeDisplayName, formatClockTime } from "@/features/realtime/utils";

import { initials } from "../utils";

export function DashboardPresence({
  rows,
  directory,
  limit,
}: {
  rows: PresenceRow[];
  directory: EmployeeDirectory;
  limit?: number;
}) {
  const t = useTranslations("realtime.presence");
  const root = useTranslations("realtime");
  const status = useTranslations("realtime.status");
  const dashboard = useTranslations("dashboard.presence");
  const locale = useLocale();
  const visible = limit === undefined ? rows : rows.slice(0, limit);

  return (
    <Card
      data-testid="dashboard-presence"
      className="gap-0 rounded-md border-border py-0 shadow-none"
    >
      <CardHeader className="flex flex-row items-start justify-between gap-3 px-5 pt-5 pb-4">
        <div className="space-y-1">
          <CardTitle>{t("title")}</CardTitle>
          <CardDescription>{t("description")}</CardDescription>
        </div>
        <Link
          href="/command-center"
          className="inline-flex min-h-8 shrink-0 items-center text-sm font-medium text-primary underline-offset-4 hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
        >
          {dashboard("openBoard")}
        </Link>
      </CardHeader>

      {visible.length === 0 ? (
        <CardContent className="px-5 pb-5">
          <EmptyState icon={Users} message={t("empty")} />
        </CardContent>
      ) : (
        <>
          <ul aria-label={t("listLabel")} className="flex flex-col">
            {visible.map((row) => {
              const name = employeeDisplayName(
                row.employee_id,
                directory,
                root("employeeFallback", { id: row.employee_id.slice(-4) }),
              );
              const code = employeeCode(row.employee_id, directory);
              return (
                <li
                  key={row.employee_id}
                  className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-border px-5 py-2.5 last:border-b-0"
                >
                  <Avatar className="size-8">
                    <AvatarFallback className="text-xs">{initials(name)}</AvatarFallback>
                  </Avatar>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium break-words sm:truncate">{name}</p>
                    {code ? (
                      <p className="font-mono text-xs text-muted-foreground">{code}</p>
                    ) : null}
                  </div>
                  <div className="flex w-full items-center justify-between gap-2 pl-11 sm:w-auto sm:justify-end sm:pl-0">
                    <Badge variant={presenceStatusVariant[row.status]}>{status(row.status)}</Badge>
                    <time
                      dateTime={row.since ?? undefined}
                      className="w-16 shrink-0 text-right font-mono text-xs whitespace-nowrap text-muted-foreground tabular-nums"
                    >
                      {row.since ? formatClockTime(row.since, locale) : "—"}
                    </time>
                  </div>
                </li>
              );
            })}
          </ul>
          <div className="border-t border-border px-5 py-2.5 text-xs text-muted-foreground">
            {dashboard("showing", { shown: visible.length, total: rows.length })}
          </div>
        </>
      )}
    </Card>
  );
}
