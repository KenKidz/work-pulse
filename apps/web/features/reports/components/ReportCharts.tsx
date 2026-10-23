"use client";

/**
 * One Recharts line chart per report — the visual reading of the same
 * normalized rows the table below renders. One series per group path, colors
 * from the `--chart-1..5` token ramp, animation off (ops surfaces stay still;
 * the table is the accessible, exact view).
 */

import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { NormalizedReport } from "../utils";
import { formatMetricAxis, formatMetricValue } from "../utils";

const SERIES_COLORS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
] as const;

function seriesColor(index: number): string {
  return SERIES_COLORS[index % SERIES_COLORS.length] ?? "var(--chart-1)";
}

/** Bound the SVG work for a 24-month daily report with many groups. */
const MAX_CHART_SERIES = 12;

export function ReportCharts({ report }: { report: NormalizedReport }) {
  const { chart } = report;
  if (chart.points.length === 0) return null;

  const plottedSeries = chart.series.slice(0, MAX_CHART_SERIES);
  const hiddenSeries = chart.series.length - plottedSeries.length;

  return (
    <div data-testid="report-chart" className="w-full">
      <div
        role="img"
        aria-label={`${chart.metric.label} by ${report.groupColumnLabel.toLowerCase()}, charted from the same rows shown in the table below.`}
        className="h-80 w-full"
      >
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={chart.points} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
            <XAxis
              dataKey="label"
              tick={{ fill: "var(--muted-foreground)", fontSize: 12 }}
              tickLine={false}
              axisLine={{ stroke: "var(--border)" }}
              minTickGap={16}
            />
            <YAxis
              width={56}
              tick={{ fill: "var(--muted-foreground)", fontSize: 12 }}
              tickLine={false}
              axisLine={false}
              tickFormatter={(value) => formatMetricAxis(Number(value), chart.metric.format)}
            />
            <Tooltip
              formatter={(value) => formatMetricValue(Number(value), chart.metric.format)}
              contentStyle={{
                background: "var(--popover)",
                border: "1px solid var(--border)",
                borderRadius: "var(--radius)",
                color: "var(--popover-foreground)",
                fontSize: 12,
              }}
              labelStyle={{ color: "var(--muted-foreground)" }}
              itemStyle={{ color: "var(--popover-foreground)" }}
            />
            <Legend wrapperStyle={{ color: "var(--muted-foreground)", fontSize: 12 }} />
            {plottedSeries.map((series, index) => (
              <Line
                key={series.key}
                type="monotone"
                dataKey={series.key}
                name={series.label}
                stroke={seriesColor(index)}
                strokeWidth={2}
                dot={chart.points.length <= 12}
                activeDot={{ r: 4 }}
                connectNulls
                isAnimationActive={false}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
      {hiddenSeries > 0 ? (
        <p className="mt-2 text-xs text-muted-foreground">
          Chart shows the first {MAX_CHART_SERIES} groups; the table has all {chart.series.length}.
        </p>
      ) : null}
    </div>
  );
}
