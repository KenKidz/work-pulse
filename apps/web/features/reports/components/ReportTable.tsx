"use client";

/**
 * The exact rows behind the chart — period, group path and every metric.
 * Numbers are mono tabular so live-length values never shift the grid; the
 * scrollable table region is keyboard reachable via `containerLabel`.
 */

import { useLocale, useTranslations } from "next-intl";

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
  const t = useTranslations("reports.report");
  const units = useTranslations("reports.units");
  const locale = useLocale();
  const rows = report.rows.slice(0, MAX_TABLE_ROWS);
  const hiddenRows = report.rows.length - rows.length;

  return (
    <>
      <p className="text-sm text-muted-foreground sm:hidden">{t("tableSwipe")}</p>
      <Table
        data-testid="reports-table"
        containerLabel={t("tableContainer")}
      >
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead>{t("period")}</TableHead>
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
                {formatBucket(row.bucket, interval, locale, t("total"), (date) =>
                  t("weekOf", { date }),
                )}
              </TableCell>
              <TableCell className="font-medium">{groupPath(row.groups)}</TableCell>
              {report.metrics.map((metric) => (
                <TableCell
                  key={metric.key}
                  data-testid={`report-metric-${metric.key}`}
                  className="text-right font-mono text-xs tabular-nums"
                >
                  {formatMetricValue(
                    row.values[metric.key] ?? 0,
                    metric.format,
                    locale,
                    units("hourShort"),
                    units("minuteShort"),
                  )}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {hiddenRows > 0 ? (
        <p data-testid="report-row-limit" className="mt-2 text-xs text-muted-foreground">
          {t("rowLimit", { shown: MAX_TABLE_ROWS, total: report.rows.length })}
        </p>
      ) : null}
    </>
  );
}
