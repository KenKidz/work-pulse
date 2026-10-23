"use client";

/**
 * The exact rows behind the chart — period, group path and every metric.
 * Numbers are mono tabular so live-length values never shift the grid; the
 * scrollable table region is keyboard reachable via `containerLabel`.
 */

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

import type { ReportInterval } from "../types";
import type { NormalizedReport } from "../utils";
import { formatBucket, formatMetricValue, groupPath } from "../utils";

/** Keep the initial DOM bounded; the note says exactly what is hidden. */
const MAX_TABLE_ROWS = 200;

export function ReportTable({
  report,
  interval,
}: {
  report: NormalizedReport;
  interval: ReportInterval;
}) {
  const rows = report.rows.slice(0, MAX_TABLE_ROWS);
  const hiddenRows = report.rows.length - rows.length;

  return (
    <>
      <p className="text-sm text-muted-foreground sm:hidden">
        Swipe sideways to see every metric field.
      </p>
      <Table
        data-testid="reports-table"
        containerLabel="Report rows. Scroll sideways to see every metric."
      >
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead>Period</TableHead>
            <TableHead>{report.groupColumnLabel}</TableHead>
            {report.metrics.map((metric) => (
              <TableHead key={metric.key} className="text-right">
                {metric.label}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.id} data-testid="report-row">
              <TableCell className="font-mono text-xs text-muted-foreground">
                {formatBucket(row.bucket, interval)}
              </TableCell>
              <TableCell className="font-medium">{groupPath(row.groups)}</TableCell>
              {report.metrics.map((metric) => (
                <TableCell
                  key={metric.key}
                  data-testid={`report-metric-${metric.key}`}
                  className="text-right font-mono text-xs tabular-nums"
                >
                  {formatMetricValue(row.values[metric.key] ?? 0, metric.format)}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {hiddenRows > 0 ? (
        <p data-testid="report-row-limit" className="mt-2 text-xs text-muted-foreground">
          Showing the first {MAX_TABLE_ROWS} of {report.rows.length} rows. Narrow the date range or
          grouping to see the rest.
        </p>
      ) : null}
    </>
  );
}
