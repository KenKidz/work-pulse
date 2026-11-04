"use client";

/**
 * Employees list — filter toolbar (`q`, department, position, location,
 * status, manager), status chips, result count, pagination and row navigation
 * to the detail page. Loading/empty/error states per the pinned AC12. The
 * create action lives in the page header (`NewEmployeeButton`). Client leaf.
 */

import { CircleAlert, Search, Users } from "lucide-react";
import Link from "next/link";
import { useGetCurrentEmployee } from "@workpulse/api-client";
import { useTranslations } from "next-intl";
import { useMemo, useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/empty-state";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

import { useDebouncedValue } from "@/hooks/use-debounced-value";

import { useAuth } from "@/features/auth/components/AuthGate";
import { useDepartments } from "@/features/organizations/hooks/use-departments";
import { usePositions } from "@/features/organizations/hooks/use-positions";
import { useLocations } from "@/features/organizations/hooks/use-locations";

import { statusVariant } from "../components/labels";
import { useEmployeeDirectory } from "../hooks/use-employee-directory";
import { useEmployees } from "../hooks/use-employees";
import type { EmployeeStatus, ListEmployeesParams } from "../types";

export const ANY = "any";

/**
 * Filter bar. Clearing a filter must drop the parameter (`undefined`), never
 * send `null`: the generated client only omits undefined and serializes null
 * as the literal string "null" → the API 422s on enum/UUID parsing.
 */
function FiltersRow({
  params,
  onChange,
  canClear,
  onClear,
}: {
  params: ListEmployeesParams;
  onChange: (next: Partial<ListEmployeesParams>) => void;
  canClear: boolean;
  onClear: () => void;
}) {
  const t = useTranslations("employees");
  const common = useTranslations("common.actions");

  return (
    <div
      className="grid min-w-0 grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-[minmax(0,1.4fr)_repeat(5,minmax(0,0.8fr))_auto]"
      data-testid="employee-filters"
    >
      <div className="relative sm:col-span-2 lg:col-span-2 xl:col-span-2 2xl:col-span-1">
        <Search
          aria-hidden="true"
          className="absolute top-1/2 left-2 size-4 -translate-y-1/2 text-muted-foreground"
        />
        <Input
          aria-label={t("filters.searchAria")}
          placeholder={t("filters.searchPlaceholder")}
          className="w-full pl-8"
          value={params.q ?? ""}
          onChange={(event) => onChange({ q: event.target.value || undefined })}
        />
      </div>

      <Select
        value={params.department_id ?? ANY}
        onValueChange={(value) =>
          onChange({ department_id: value === ANY ? undefined : value, page: 1 })
        }
      >
        <SelectTrigger aria-label={t("filters.departmentAria")} className="w-full">
          <SelectValue placeholder={t("filters.allDepartments")} />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ANY}>{t("filters.allDepartments")}</SelectItem>
          <DepartmentsOptions />
        </SelectContent>
      </Select>

      <Select
        value={params.position_id ?? ANY}
        onValueChange={(value) =>
          onChange({ position_id: value === ANY ? undefined : value, page: 1 })
        }
      >
        <SelectTrigger aria-label={t("filters.positionAria")} className="w-full">
          <SelectValue placeholder={t("filters.allPositions")} />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ANY}>{t("filters.allPositions")}</SelectItem>
          <PositionsOptions />
        </SelectContent>
      </Select>

      <Select
        value={params.location_id ?? ANY}
        onValueChange={(value) =>
          onChange({ location_id: value === ANY ? undefined : value, page: 1 })
        }
      >
        <SelectTrigger aria-label={t("filters.locationAria")} className="w-full">
          <SelectValue placeholder={t("filters.allLocations")} />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ANY}>{t("filters.allLocations")}</SelectItem>
          <LocationsOptions />
        </SelectContent>
      </Select>

      <Select
        value={params.status ?? ANY}
        onValueChange={(value) =>
          onChange({ status: value === ANY ? undefined : (value as EmployeeStatus), page: 1 })
        }
      >
        <SelectTrigger aria-label={t("filters.statusAria")} className="w-full">
          <SelectValue placeholder={t("filters.allStatuses")} />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ANY}>{t("filters.allStatuses")}</SelectItem>
          <SelectItem value="active">{t("status.active")}</SelectItem>
          <SelectItem value="on_leave">{t("status.on_leave")}</SelectItem>
          <SelectItem value="terminated">{t("status.terminated")}</SelectItem>
        </SelectContent>
      </Select>

      <Select
        value={params.manager_id ?? ANY}
        onValueChange={(value) =>
          onChange({ manager_id: value === ANY ? undefined : value, page: 1 })
        }
      >
        <SelectTrigger aria-label={t("filters.managerAria")} className="w-full">
          <SelectValue placeholder={t("filters.allManagers")} />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ANY}>{t("filters.allManagers")}</SelectItem>
          <ManagerOptions />
        </SelectContent>
      </Select>

      {canClear ? (
        <Button
          variant="ghost"
          size="sm"
          className="min-h-11 justify-self-end sm:min-h-7"
          onClick={onClear}
        >
          {common("clear")}
        </Button>
      ) : null}
    </div>
  );
}

/** Manager filter options — a fixed unfiltered employees query. */
function ManagerOptions() {
  const managersQuery = useEmployees({ page_size: 100 });
  const items = managersQuery.data?.status === 200 ? managersQuery.data.data.items : [];
  return (
    <>
      {items.map((employee) => (
        <SelectItem key={employee.id} value={employee.id}>
          {employee.first_name} {employee.last_name}
        </SelectItem>
      ))}
    </>
  );
}

/** Non-archived organization options for the filter selects. */
function DepartmentsOptions() {
  const departmentsQuery = useDepartments({ include_archived: false, page_size: 100 });
  const items = departmentsQuery.data?.status === 200 ? departmentsQuery.data.data.items : [];
  return (
    <>
      {items.map((department) => (
        <SelectItem key={department.id} value={department.id}>
          {department.name}
        </SelectItem>
      ))}
    </>
  );
}

function PositionsOptions() {
  const positionsQuery = usePositions({ include_archived: false, page_size: 100 });
  const items = positionsQuery.data?.status === 200 ? positionsQuery.data.data.items : [];
  return (
    <>
      {items.map((position) => (
        <SelectItem key={position.id} value={position.id}>
          {position.title}
        </SelectItem>
      ))}
    </>
  );
}

function LocationsOptions() {
  const locationsQuery = useLocations({ include_archived: false, page_size: 100 });
  const items = locationsQuery.data?.status === 200 ? locationsQuery.data.data.items : [];
  return (
    <>
      {items.map((location) => (
        <SelectItem key={location.id} value={location.id}>
          {location.name}
        </SelectItem>
      ))}
    </>
  );
}

/** Page size for the employees list query (pickers use their own). */
export const EMPLOYEES_PAGE_SIZE = 20;

export function EmployeesTable() {
  const t = useTranslations("employees");
  const common = useTranslations("common.actions");
  const [params, setParams] = useState<ListEmployeesParams>({
    page: 1,
    page_size: EMPLOYEES_PAGE_SIZE,
  });
  // The search box state — fed to the query through useDebouncedValue so a
  // typed query costs one list request on a 300 ms pause, not one per
  // keystroke. Typing resets the page immediately; only the API call waits.
  const [searchInput, setSearchInput] = useState("");
  const debouncedSearch = useDebouncedValue(searchInput);

  const employeesQuery = useEmployees({ ...params, q: debouncedSearch || undefined });
  const listResponse = employeesQuery.data;
  const employees = listResponse && listResponse.status === 200 ? listResponse.data.items : [];
  const page = listResponse && listResponse.status === 200 ? listResponse.data.page : null;
  // The typed value, not the debounced one, so Clear appears on the first
  // keystroke; only the request itself waits for the pause.
  const hasFilters = Boolean(
    searchInput ||
    params.department_id ||
    params.position_id ||
    params.location_id ||
    params.status ||
    params.manager_id,
  );

  // Label maps for the visible org columns. The same query params as the
  // filter selects share one React Query cache entry.
  const { user } = useAuth();
  const directoryQuery = useEmployeeDirectory();
  const meQuery = useGetCurrentEmployee({ query: { enabled: user.employee_id !== null } });
  const departmentsQuery = useDepartments({ include_archived: false, page_size: 100 });
  const positionsQuery = usePositions({ include_archived: false, page_size: 100 });
  const locationsQuery = useLocations({ include_archived: false, page_size: 100 });
  const departmentNames = useMemo(
    () =>
      new Map(
        (departmentsQuery.data?.status === 200 ? departmentsQuery.data.data.items : []).map(
          (department) => [department.id, department.name],
        ),
      ),
    [departmentsQuery.data],
  );
  const positionNames = useMemo(
    () =>
      new Map(
        (positionsQuery.data?.status === 200 ? positionsQuery.data.data.items : []).map(
          (position) => [position.id, position.title],
        ),
      ),
    [positionsQuery.data],
  );
  const locationNames = useMemo(
    () =>
      new Map(
        (locationsQuery.data?.status === 200 ? locationsQuery.data.data.items : []).map(
          (location) => [location.id, location.name],
        ),
      ),
    [locationsQuery.data],
  );
  const managerNames = useMemo(() => {
    const names = new Map(
      (directoryQuery.data ?? []).map((employee) => [
        employee.id,
        `${employee.first_name} ${employee.last_name}`.trim(),
      ]),
    );
    // A manager's own employee is not in the reports-only list, yet every row's
    // manager_id points at them. Merge the signed-in employee so the column
    // resolves instead of reading "—" for every report.
    const me = meQuery.data?.status === 200 ? meQuery.data.data : null;
    if (me !== null) {
      names.set(me.id, `${me.first_name} ${me.last_name}`.trim());
    }
    return names;
  }, [directoryQuery.data, meQuery.data]);

  function clearFilters() {
    setSearchInput("");
    setParams({ page: 1, page_size: EMPLOYEES_PAGE_SIZE });
  }

  function change(next: Partial<ListEmployeesParams>) {
    // The q input goes through the debounce; selects commit directly.
    if ("q" in next) {
      setSearchInput(next.q ?? "");
      setParams((current) => (current.page === 1 ? current : { ...current, page: 1 }));
      return;
    }
    setParams((current) => ({ ...current, ...next }));
  }

  return (
    <Card className="gap-0 overflow-hidden rounded-md border-border py-0 shadow-none">
      <div className="border-b bg-muted/50 px-4 py-3 sm:px-5">
        <FiltersRow
          params={{ ...params, q: searchInput }}
          onChange={change}
          canClear={hasFilters}
          onClear={clearFilters}
        />
      </div>

      {employeesQuery.isPending ? (
        <div className="flex items-center justify-center gap-2 px-6 py-10 text-sm text-muted-foreground">
          <Spinner className="size-4" />
          <p>{t("table.loading")}</p>
        </div>
      ) : employeesQuery.isError ? (
        <div className="p-4">
          <Alert variant="destructive">
            <CircleAlert aria-hidden="true" />
            <AlertDescription>
              <p>{t("table.loadError")}</p>
              <Button
                variant="outline"
                size="sm"
                className="mt-2"
                onClick={() => void employeesQuery.refetch()}
              >
                {common("retry")}
              </Button>
            </AlertDescription>
          </Alert>
        </div>
      ) : employees.length === 0 ? (
        <div className="p-4">
          <EmptyState
            icon={Users}
            message={hasFilters ? t("table.emptyFiltered") : t("table.empty")}
          />
          {hasFilters ? (
            <div className="mt-3 flex justify-center">
              <Button variant="outline" size="sm" onClick={clearFilters}>
                {t("table.clearFilters")}
              </Button>
            </div>
          ) : null}
        </div>
      ) : (
        <>
          <p className="px-4 py-2 text-xs text-muted-foreground sm:hidden">{t("table.swipe")}</p>
          <Table aria-label={t("table.label")} containerLabel={t("table.containerLabel")}>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead className="pl-3">{t("table.columns.employee")}</TableHead>
                <TableHead>{t("table.columns.email")}</TableHead>
                <TableHead>{t("table.columns.role")}</TableHead>
                <TableHead>{t("table.columns.location")}</TableHead>
                <TableHead>{t("table.columns.manager")}</TableHead>
                <TableHead>{t("table.columns.type")}</TableHead>
                <TableHead className="pr-3">{t("table.columns.status")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {employees.map((employee) => {
                const name = `${employee.first_name} ${employee.last_name}`.trim();
                return (
                  <TableRow key={employee.id}>
                    <TableCell className="pl-3">
                      <div className="flex items-center gap-2.5">
                        <Avatar className="size-8 shrink-0">
                          <AvatarFallback className="text-xs">
                            {initials(employee.first_name, employee.last_name)}
                          </AvatarFallback>
                        </Avatar>
                        <div className="min-w-0">
                          <Link
                            className="block max-w-[12rem] truncate font-medium underline-offset-4 hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
                            href={`/employees/${employee.id}`}
                            title={name}
                          >
                            {name}
                          </Link>
                          <span className="block font-mono text-xs text-muted-foreground">
                            {employee.employee_code}
                          </span>
                        </div>
                      </div>
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      <span className="block max-w-[12rem] truncate" title={employee.email}>
                        {employee.email}
                      </span>
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-col">
                        <span>{positionNames.get(employee.position_id) ?? "—"}</span>
                        <span className="text-xs text-muted-foreground">
                          {departmentNames.get(employee.department_id) ?? "—"}
                        </span>
                      </div>
                    </TableCell>
                    <TableCell>{locationNames.get(employee.location_id) ?? "—"}</TableCell>
                    <TableCell className="text-muted-foreground">
                      {employee.manager_id ? (managerNames.get(employee.manager_id) ?? "—") : "—"}
                    </TableCell>
                    <TableCell>{t(`types.${employee.employment_type}`)}</TableCell>
                    <TableCell className="pr-3">
                      {employee.status === "active" ? (
                        <span className="text-sm text-muted-foreground" data-status="active">
                          {t("status.active")}
                        </span>
                      ) : (
                        <Badge
                          variant={statusVariant[employee.status]}
                          data-status={employee.status}
                        >
                          {t(`status.${employee.status}`)}
                        </Badge>
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>

          {page ? (
            <div className="flex flex-wrap items-center justify-between gap-2 border-t px-4 py-2.5 text-xs text-muted-foreground">
              <p className="tabular-nums">{t("table.showing", { count: page.total_items })}</p>
              {page.total_pages > 1 ? (
                <div className="ml-auto flex items-center gap-2 text-sm">
                  <Button
                    variant="outline"
                    size="sm"
                    aria-label={t("table.previousAria")}
                    disabled={page.page <= 1}
                    onClick={() => change({ page: page.page - 1 })}
                  >
                    {t("table.previous")}
                  </Button>
                  <span className="tabular-nums">
                    {t("table.pageOf", { page: page.page, total: page.total_pages })}
                  </span>
                  <Button
                    variant="outline"
                    size="sm"
                    aria-label={t("table.nextAria")}
                    disabled={page.page >= page.total_pages}
                    onClick={() => change({ page: page.page + 1 })}
                  >
                    {t("table.next")}
                  </Button>
                </div>
              ) : null}
            </div>
          ) : null}
        </>
      )}
    </Card>
  );
}

/** `Alice Adams` → `AA` for the avatar fallback. */
function initials(firstName: string, lastName: string): string {
  return `${firstName[0] ?? ""}${lastName[0] ?? ""}`.toUpperCase();
}
