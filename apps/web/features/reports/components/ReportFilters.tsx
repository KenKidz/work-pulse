"use client";

/**
 * The shared filters above every report tab. Date range, interval, grouping
 * order (a checkbox menu, because the API takes the dimensions in row order)
 * and the three dimension filters. Controls commit immediately; only inputs
 * that refetch per keystroke would need the debounce hook, and none do here.
 */

import { ChevronDown } from "lucide-react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import { useReportFilterOptions } from "../hooks/use-reports";
import {
  REPORT_DIMENSIONS,
  LEAVE_REPORT_DIMENSIONS,
  type ReportDimension,
  type ReportFiltersState,
  type ReportInterval,
  type ReportKind,
} from "../types";
import {
  REPORT_FILTER_OPTION_MAX_PAGES,
  REPORT_FILTER_OPTION_PAGE_SIZE,
  effectiveGroupBy,
  reportKindDimensions,
  toggleGroupDimension,
} from "../utils";

const ANY = "__any__";
const INTERVAL_VALUES: readonly ReportInterval[] = ["day", "week", "month", "total"];

export function ReportFilters({
  kind,
  filters,
  onChange,
}: {
  kind: ReportKind;
  filters: ReportFiltersState;
  onChange: (next: ReportFiltersState) => void;
}) {
  const t = useTranslations("reports.filters");
  const dimensions = useTranslations("reports.dimensions");
  const intervals = useTranslations("reports.intervals");
  const { departments, positions, locations, truncated } = useReportFilterOptions();
  const allowed = reportKindDimensions(kind);
  const effective = effectiveGroupBy(kind, filters.groupBy);
  const triggerLabel = effective.map((dimension) => dimensions(dimension)).join(" · ");

  function update(patch: Partial<ReportFiltersState>) {
    onChange({ ...filters, ...patch });
  }

  function toggleDimension(dimension: ReportDimension) {
    update({ groupBy: toggleGroupDimension(filters.groupBy, dimension, allowed) });
  }

  return (
    <div
      className="flex flex-col gap-2 lg:flex-row lg:flex-wrap lg:items-center"
      data-testid="report-filters"
    >
      <div className="grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-2 sm:flex">
        <Input
          aria-label={t("fromDate")}
          type="date"
          className="w-full sm:w-40"
          value={filters.from}
          onChange={(event) => update({ from: event.target.value })}
        />
        <span className="text-sm text-muted-foreground">{t("to")}</span>
        <Input
          aria-label={t("toDate")}
          type="date"
          className="w-full sm:w-40"
          value={filters.to}
          onChange={(event) => update({ to: event.target.value })}
        />
      </div>

      <Select
        value={filters.interval}
        onValueChange={(value) => update({ interval: value as ReportFiltersState["interval"] })}
      >
        <SelectTrigger aria-label={t("intervalAria")} className="w-full sm:w-40">
          <SelectValue placeholder={intervals("month")} />
        </SelectTrigger>
        <SelectContent>
          {INTERVAL_VALUES.map((interval) => (
            <SelectItem key={interval} value={interval}>
              {intervals(interval)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="outline"
            aria-label={t("groupByAria")}
            className="w-full justify-between sm:w-64"
          >
            <span className="truncate">{t("groupBy", { dimensions: triggerLabel })}</span>
            <ChevronDown aria-hidden="true" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-64">
          <DropdownMenuLabel>{t("groupByInOrder")}</DropdownMenuLabel>
          <DropdownMenuSeparator />
          {(kind === "leave" ? LEAVE_REPORT_DIMENSIONS : REPORT_DIMENSIONS).map((dimension) => {
            const checked = filters.groupBy.includes(dimension);
            const atLimit = !checked && effective.length >= 3;
            return (
              <DropdownMenuCheckboxItem
                key={dimension}
                checked={checked}
                disabled={atLimit || (checked && effective.length === 1)}
                onSelect={(event) => event.preventDefault()}
                onCheckedChange={() => toggleDimension(dimension)}
              >
                {dimensions(dimension)}
              </DropdownMenuCheckboxItem>
            );
          })}
        </DropdownMenuContent>
      </DropdownMenu>

      <div className="grid grid-cols-1 gap-2 sm:flex">
        <Select
          value={filters.departmentId ?? ANY}
          onValueChange={(value) => update({ departmentId: value === ANY ? undefined : value })}
        >
          <SelectTrigger aria-label={t("departmentAria")} className="w-full sm:w-44">
            <SelectValue placeholder={t("allDepartments")} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ANY}>{t("allDepartments")}</SelectItem>
            {departments.map((department) => (
              <SelectItem key={department.id} value={department.id}>
                {department.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select
          value={filters.locationId ?? ANY}
          onValueChange={(value) => update({ locationId: value === ANY ? undefined : value })}
        >
          <SelectTrigger aria-label={t("locationAria")} className="w-full sm:w-40">
            <SelectValue placeholder={t("allLocations")} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ANY}>{t("allLocations")}</SelectItem>
            {locations.map((location) => (
              <SelectItem key={location.id} value={location.id}>
                {location.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select
          value={filters.positionId ?? ANY}
          onValueChange={(value) => update({ positionId: value === ANY ? undefined : value })}
        >
          <SelectTrigger aria-label={t("positionAria")} className="w-full sm:w-44">
            <SelectValue placeholder={t("allPositions")} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ANY}>{t("allPositions")}</SelectItem>
            {positions.map((position) => (
              <SelectItem key={position.id} value={position.id}>
                {position.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {truncated ? (
        <p
          data-testid="report-filter-options-truncated"
          className="text-xs text-muted-foreground lg:basis-full"
        >
          {t("truncated", {
            limit: REPORT_FILTER_OPTION_PAGE_SIZE * REPORT_FILTER_OPTION_MAX_PAGES,
          })}
        </p>
      ) : null}
    </div>
  );
}
