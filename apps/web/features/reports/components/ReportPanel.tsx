"use client";

/**
 * One report's panel — loading, error, empty and success states around the
 * chart + table pair. The panel is presentational; the workspace owns filters
 * and the query state. Empty and error states are the designed surface, not a
 * fallback (workpulse-ui §7).
 */

import { useMemo } from "react";
import {
  CalendarClock,
  CalendarDays,
  ChartColumn,
  CircleAlert,
  RefreshCw,
  Timer,
  Users,
  type LucideIcon,
} from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/empty-state";
import { Spinner } from "@/components/ui/spinner";

import {
  REPORT_EMPTY_MESSAGES,
  REPORT_TAB_DESCRIPTIONS,
  REPORT_TAB_LABELS,
  intervalLabel,
  isReportErrorRetryable,
  reportErrorMessage,
} from "../labels";
import type { ReportKind } from "../types";
import { formatRange, normalizeReport } from "../utils";
import type { ReportState } from "../hooks/use-reports";
import { ReportCharts } from "./ReportCharts";
import { ReportTable } from "./ReportTable";

const KIND_ICONS: Record<ReportKind, LucideIcon> = {
  overtime: Timer,
  attendance: CalendarClock,
  leave: CalendarDays,
  headcount: Users,
};

function LoadingPanel() {
  return (
    <Card
      data-testid="report-loading"
      className="min-h-80 items-center justify-center rounded-md border-border shadow-none"
    >
      <div className="flex flex-col items-center gap-2 p-8 text-sm text-muted-foreground">
        <Spinner className="size-4" />
        <p>Loading report…</p>
      </div>
    </Card>
  );
}

function ErrorPanel({ state }: { state: ReportState }) {
  const retryable = isReportErrorRetryable(state.error);
  return (
    <Card className="rounded-md border-border shadow-none">
      <div className="p-4 sm:p-5">
        <Alert variant="destructive" data-testid="report-error">
          <CircleAlert aria-hidden="true" />
          <AlertTitle>{retryable ? "Report unavailable" : "Access denied"}</AlertTitle>
          <AlertDescription>
            <p>{reportErrorMessage(state.error)}</p>
            {retryable ? (
              <Button
                variant="outline"
                size="sm"
                className="mt-2 min-h-11 sm:min-h-7"
                onClick={state.refetch}
              >
                <RefreshCw aria-hidden="true" />
                Try again
              </Button>
            ) : null}
          </AlertDescription>
        </Alert>
      </div>
    </Card>
  );
}

function EmptyPanel({ kind }: { kind: ReportKind }) {
  return (
    <Card className="rounded-md border-border shadow-none">
      <div className="p-4 sm:p-5">
        <EmptyState icon={ChartColumn} message={REPORT_EMPTY_MESSAGES[kind]} />
      </div>
    </Card>
  );
}

export function ReportPanel({ state }: { state: ReportState }) {
  const { kind, report } = state;
  // The pivot sorts and groups every row; recomputing it on each render (for
  // example when the tab list re-renders) is wasted work on large reports.
  const normalized = useMemo(
    () => (report === null ? null : normalizeReport(kind, report)),
    [kind, report],
  );

  if (state.isPending) return <LoadingPanel />;
  if (state.isError) return <ErrorPanel state={state} />;
  if (report === null || report.rows.length === 0 || normalized === null) {
    return <EmptyPanel kind={kind} />;
  }

  const Icon = KIND_ICONS[kind];

  return (
    <Card
      data-testid="report-panel"
      className="gap-0 overflow-hidden rounded-md border-border py-0 shadow-none"
    >
      <CardHeader className="border-b px-4 py-4 sm:px-5">
        <CardTitle className="flex items-center gap-2">
          <Icon className="size-4 text-muted-foreground" aria-hidden="true" />
          {REPORT_TAB_LABELS[kind]}
        </CardTitle>
        <CardDescription>
          {REPORT_TAB_DESCRIPTIONS[kind]} {formatRange(report.from, report.to)} ·{" "}
          {intervalLabel(report.interval)} · {normalized.groupColumnLabel}
        </CardDescription>
      </CardHeader>

      {normalized.chart.points.length > 0 ? (
        <div className="border-b px-4 py-4 sm:px-5">
          <ReportCharts report={normalized} />
        </div>
      ) : null}

      <div className="p-4 sm:p-5">
        <ReportTable report={normalized} interval={report.interval} />
      </div>
    </Card>
  );
}
