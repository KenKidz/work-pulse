"use client";

/**
 * The shared filters above every report tab. Date range, interval, grouping
 * order (a checkbox menu, because the API takes the dimensions in row order)
 * and the three dimension filters. Controls commit immediately; only inputs
 * that refetch per keystroke would need the debounce hook, and none do here.
 */

import { ChevronDown } from "lucide-react";

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
import { INTERVAL_OPTIONS, REPORT_DIMENSION_LABELS } from "../labels";
import {
  REPORT_DIMENSIONS,
  LEAVE_REPORT_DIMENSIONS,
  type ReportDimension,
  type ReportFiltersState,
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

export function ReportFilters({
  kind,
  filters,
  onChange,
}: {
  kind: ReportKind;
  filters: ReportFiltersState;
  onChange: (next: ReportFiltersState) => void;
}) {
  const { departments, positions, locations, truncated } = useReportFilterOptions();
  const allowed = reportKindDimensions(kind);
  const effective = effectiveGroupBy(kind, filters.groupBy);
  const triggerLabel = effective.map((dimension) => REPORT_DIMENSION_LABELS[dimension]).join(" · ");

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
          aria-label="From date"
          type="date"
          className="w-full sm:w-40"
          value={filters.from}
          onChange={(event) => update({ from: event.target.value })}
        />
        <span className="text-sm text-muted-foreground">to</span>
        <Input
          aria-label="To date"
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
        <SelectTrigger aria-label="Interval" className="w-full sm:w-40">
          <SelectValue placeholder="Monthly" />
        </SelectTrigger>
        <SelectContent>
          {INTERVAL_OPTIONS.map((option) => (
            <SelectItem key={option.value} value={option.value}>
              {option.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="outline"
            aria-label="Group by dimensions"
            className="w-full justify-between sm:w-64"
          >
            <span className="truncate">Group by: {triggerLabel}</span>
            <ChevronDown aria-hidden="true" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-64">
          <DropdownMenuLabel>Group by (in row order)</DropdownMenuLabel>
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
                {REPORT_DIMENSION_LABELS[dimension]}
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
          <SelectTrigger aria-label="Filter by department" className="w-full sm:w-44">
            <SelectValue placeholder="All departments" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ANY}>All departments</SelectItem>
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
          <SelectTrigger aria-label="Filter by location" className="w-full sm:w-40">
            <SelectValue placeholder="All locations" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ANY}>All locations</SelectItem>
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
          <SelectTrigger aria-label="Filter by position" className="w-full sm:w-44">
            <SelectValue placeholder="All positions" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ANY}>All positions</SelectItem>
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
          Some filter options are hidden because this workspace has more than{" "}
          {REPORT_FILTER_OPTION_PAGE_SIZE * REPORT_FILTER_OPTION_MAX_PAGES} departments, positions
          or locations.
        </p>
      ) : null}
    </div>
  );
}
