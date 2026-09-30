"use client";

/**
 * Employees list — filter bar (`q`, department, position, location, status,
 * manager), status chips, pagination and row navigation to the detail page.
 * Loading/empty/error states per the pinned AC12. Client leaf.
 */

import { CircleAlert, Search, UserPlus, Users } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
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

import { useDepartments } from "@/features/organizations/hooks/use-departments";
import { usePositions } from "@/features/organizations/hooks/use-positions";
import { useLocations } from "@/features/organizations/hooks/use-locations";

import { employmentTypeLabel, statusLabel, statusVariant } from "../components/labels";
import { useEmployees } from "../hooks/use-employees";
import type { EmployeeStatus, ListEmployeesParams } from "../types";
import { EmployeeFormDialog } from "./EmployeeFormDialog";

export const ANY = "any";

/**
 * Filter bar. Clearing a filter must drop the parameter (`undefined`), never
 * send `null`: the generated client only omits undefined and serializes null
 * as the literal string "null" → the API 422s on enum/UUID parsing.
 */
function FiltersRow({
  params,
  onChange,
}: {
  params: ListEmployeesParams;
  onChange: (next: Partial<ListEmployeesParams>) => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="employee-filters">
      <div className="relative w-full sm:w-64">
        <Search className="absolute top-1/2 left-2 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          aria-label="Search employees"
          placeholder="Search name, email or code"
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
        <SelectTrigger aria-label="Filter by department" className="w-full sm:w-48">
          <SelectValue placeholder="All departments" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ANY}>All departments</SelectItem>
          <DepartmentsOptions />
        </SelectContent>
      </Select>

      <Select
        value={params.position_id ?? ANY}
        onValueChange={(value) =>
          onChange({ position_id: value === ANY ? undefined : value, page: 1 })
        }
      >
        <SelectTrigger aria-label="Filter by position" className="w-full sm:w-48">
          <SelectValue placeholder="All positions" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ANY}>All positions</SelectItem>
          <PositionsOptions />
        </SelectContent>
      </Select>

      <Select
        value={params.location_id ?? ANY}
        onValueChange={(value) =>
          onChange({ location_id: value === ANY ? undefined : value, page: 1 })
        }
      >
        <SelectTrigger aria-label="Filter by location" className="w-full sm:w-48">
          <SelectValue placeholder="All locations" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ANY}>All locations</SelectItem>
          <LocationsOptions />
        </SelectContent>
      </Select>

      <Select
        value={params.status ?? ANY}
        onValueChange={(value) =>
          onChange({ status: value === ANY ? undefined : (value as EmployeeStatus), page: 1 })
        }
      >
        <SelectTrigger aria-label="Filter by status" className="w-full sm:w-40">
          <SelectValue placeholder="All statuses" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ANY}>All statuses</SelectItem>
          <SelectItem value="active">Active</SelectItem>
          <SelectItem value="on_leave">On leave</SelectItem>
          <SelectItem value="terminated">Terminated</SelectItem>
        </SelectContent>
      </Select>

      <Select
        value={params.manager_id ?? ANY}
        onValueChange={(value) =>
          onChange({ manager_id: value === ANY ? undefined : value, page: 1 })
        }
      >
        <SelectTrigger aria-label="Filter by manager" className="w-full sm:w-48">
          <SelectValue placeholder="All managers" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ANY}>All managers</SelectItem>
          <ManagerOptions />
        </SelectContent>
      </Select>
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
  const [params, setParams] = useState<ListEmployeesParams>({
    page: 1,
    page_size: EMPLOYEES_PAGE_SIZE,
  });
  const [dialogOpen, setDialogOpen] = useState(false);
  // The search box state — fed to the query through useDebouncedValue so a
  // typed query costs one list request on a 300 ms pause, not one per
  // keystroke. Typing resets the page immediately; only the API call waits.
  const [searchInput, setSearchInput] = useState("");
  const debouncedSearch = useDebouncedValue(searchInput);

  const employeesQuery = useEmployees({ ...params, q: debouncedSearch || undefined });
  const listResponse = employeesQuery.data;
  const employees = listResponse && listResponse.status === 200 ? listResponse.data.items : [];
  const page = listResponse && listResponse.status === 200 ? listResponse.data.page : null;

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
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <FiltersRow params={{ ...params, q: searchInput }} onChange={change} />
        <Button className="ml-auto" onClick={() => setDialogOpen(true)}>
          <UserPlus />
          New employee
        </Button>
      </div>

      <Card className="gap-0 overflow-hidden rounded-md border-border py-0 shadow-none">
        {employeesQuery.isPending ? (
          <div className="flex items-center justify-center gap-2 px-6 py-10 text-sm text-muted-foreground">
            <Spinner className="size-4" />
            <p>Loading employees…</p>
          </div>
        ) : employeesQuery.isError ? (
          <div className="p-4">
            <Alert variant="destructive">
              <CircleAlert aria-hidden="true" />
              <AlertDescription>
                <p>Could not load employees.</p>
                <Button
                  variant="outline"
                  size="sm"
                  className="mt-2"
                  onClick={() => void employeesQuery.refetch()}
                >
                  Retry
                </Button>
              </AlertDescription>
            </Alert>
          </div>
        ) : employees.length === 0 ? (
          <div className="p-4">
            <EmptyState
              icon={Users}
              message="No employees match. Create the first employee record to build the workforce."
            />
          </div>
        ) : (
          <>
            <p className="px-4 py-2 text-xs text-muted-foreground sm:hidden">
              Swipe sideways to see every employee field.
            </p>
            <Table
              aria-label="Employees"
              containerLabel="Employees. Scroll horizontally to read all columns."
            >
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="pl-3">Code</TableHead>
                  <TableHead>Name</TableHead>
                  <TableHead>Email</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead className="pr-3">Status</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {employees.map((employee) => (
                  <TableRow key={employee.id}>
                    <TableCell className="pl-3 font-mono text-xs">
                      {employee.employee_code}
                    </TableCell>
                    <TableCell className="font-medium">
                      <Link
                        className="underline-offset-4 hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
                        href={`/employees/${employee.id}`}
                      >
                        {employee.first_name} {employee.last_name}
                      </Link>
                    </TableCell>
                    <TableCell className="text-muted-foreground">{employee.email}</TableCell>
                    <TableCell>{employmentTypeLabel[employee.employment_type]}</TableCell>
                    <TableCell className="pr-3">
                      <Badge variant={statusVariant[employee.status]} data-status={employee.status}>
                        {statusLabel[employee.status]}
                      </Badge>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>

            {page && page.total_pages > 1 ? (
              <div className="flex items-center justify-end gap-2 border-t px-4 py-3 text-sm">
                <Button
                  variant="outline"
                  size="sm"
                  aria-label="Previous page"
                  disabled={page.page <= 1}
                  onClick={() => change({ page: page.page - 1 })}
                >
                  Previous
                </Button>
                <span className="text-muted-foreground">
                  Page {page.page} of {page.total_pages}
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  aria-label="Next page"
                  disabled={page.page >= page.total_pages}
                  onClick={() => change({ page: page.page + 1 })}
                >
                  Next
                </Button>
              </div>
            ) : null}
          </>
        )}
      </Card>

      <EmployeeFormDialog open={dialogOpen} onOpenChange={setDialogOpen} employee={null} />
    </div>
  );
}
