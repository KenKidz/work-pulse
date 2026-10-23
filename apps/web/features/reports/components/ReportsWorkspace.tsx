"use client";

/**
 * The Reports workspace — four tabs over one shared filter bar. The active
 * tab's query is the only one enabled; the generated hooks are keyed by
 * `[reports, kind, params]`, so a filter change is a new key and a tab switch
 * reuses its cached result. Tabs are the surface's only local state.
 */

import { useState } from "react";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import { REPORT_TAB_LABELS } from "../labels";
import { REPORT_KINDS, type ReportFiltersState, type ReportKind } from "../types";
import { defaultReportFilters, effectiveGroupBy, toReportParams } from "../utils";
import {
  toReportState,
  useAttendanceReport,
  useHeadcountReport,
  useLeaveReport,
  useOvertimeReport,
} from "../hooks/use-reports";
import { ReportFilters } from "./ReportFilters";
import { ReportPanel } from "./ReportPanel";

export function ReportsWorkspace() {
  const [kind, setKind] = useState<ReportKind>("overtime");
  const [filters, setFilters] = useState<ReportFiltersState>(defaultReportFilters);

  const params = toReportParams(filters, kind);
  const overtime = useOvertimeReport(params, { enabled: kind === "overtime" });
  const attendance = useAttendanceReport(params, { enabled: kind === "attendance" });
  const leave = useLeaveReport(params, { enabled: kind === "leave" });
  const headcount = useHeadcountReport(params, { enabled: kind === "headcount" });

  const activeQuery =
    kind === "overtime"
      ? overtime
      : kind === "attendance"
        ? attendance
        : kind === "leave"
          ? leave
          : headcount;

  function changeKind(next: ReportKind) {
    setKind(next);
    // Drop a dimension the new report does not accept (`leave_type`), so the
    // filter bar and the wire params can never disagree.
    setFilters((current) => ({
      ...current,
      groupBy: effectiveGroupBy(next, current.groupBy),
    }));
  }

  return (
    <Tabs
      value={kind}
      onValueChange={(value) => changeKind(value as ReportKind)}
      className="gap-4"
      data-testid="reports-workspace"
    >
      <div className="flex flex-col gap-3">
        <TabsList aria-label="Report kind" className="w-full justify-start sm:w-fit">
          {REPORT_KINDS.map((reportKind) => (
            <TabsTrigger key={reportKind} value={reportKind} className="px-3">
              {REPORT_TAB_LABELS[reportKind]}
            </TabsTrigger>
          ))}
        </TabsList>
        <ReportFilters kind={kind} filters={filters} onChange={setFilters} />
      </div>

      <TabsContent value={kind} className="mt-0">
        <ReportPanel state={toReportState(kind, activeQuery)} />
      </TabsContent>
    </Tabs>
  );
}
