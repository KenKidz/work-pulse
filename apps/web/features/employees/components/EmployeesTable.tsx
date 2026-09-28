"use client";

/**
 * Employees list — filter bar (`q`, department, position, location, status,
 * manager), status chips, pagination and row navigation to the detail page.
 * Loading/empty/error states per the pinned AC12. Client leaf.
 */

import { Search, UserPlus } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

import { useDepartments } from "@/features/organizations/hooks/use-departments";
import { usePositions } from "@/features/organizations/hooks/use-positions";
import { useLocations } from "@/features/organizations/hooks/use-locations";

import { employmentTypeLabel, statusLabel } from "../components/labels";
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
      <div className="relative">
        <Search className="absolute top-1/2 left-2 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          aria-label="Search employees"
          placeholder="Search name, email or code"
          className="w-64 pl-8"
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
        <SelectTrigger aria-label="Filter by department" className="w-48">
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
        <SelectTrigger aria-label="Filter by position" className="w-48">
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
        <SelectTrigger aria-label="Filter by location" className="w-48">
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
        <SelectTrigger aria-label="Filter by status" className="w-40">
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
        <SelectTrigger aria-label="Filter by manager" className="w-48">
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

  const employeesQuery = useEmployees(params);
  const listResponse = employeesQuery.data;
  const employees = listResponse && listResponse.status === 200 ? listResponse.data.items : [];
  const page = listResponse && listResponse.status === 200 ? listResponse.data.page : null;

  function change(next: Partial<ListEmployeesParams>) {
    setParams((current) => ({ ...current, ...next }));
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <FiltersRow params={params} onChange={change} />
        <Button onClick={() => setDialogOpen(true)}>
          <UserPlus />
          New employee
        </Button>
      </div>

      {employeesQuery.isPending ? (
        <p className="text-sm text-muted-foreground">Loading employees…</p>
      ) : employeesQuery.isError ? (
        <div role="alert" className="rounded-md border border-destructive/40 p-4 text-sm">
          <p>Could not load employees.</p>
          <Button
            variant="outline"
            size="sm"
            className="mt-3"
            onClick={() => void employeesQuery.refetch()}
          >
            Retry
          </Button>
        </div>
      ) : employees.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No employees match. Create the first employee record to build the workforce.
        </p>
      ) : (
        <>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Code</TableHead>
                <TableHead>Name</TableHead>
                <TableHead>Email</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {employees.map((employee) => (
                <TableRow key={employee.id}>
                  <TableCell className="font-mono text-xs">{employee.employee_code}</TableCell>
                  <TableCell className="font-medium">
                    <Link className="hover:underline" href={`/employees/${employee.id}`}>
                      {employee.first_name} {employee.last_name}
                    </Link>
                  </TableCell>
                  <TableCell className="text-muted-foreground">{employee.email}</TableCell>
                  <TableCell>{employmentTypeLabel[employee.employment_type]}</TableCell>
                  <TableCell>
                    <span
                      data-status={employee.status}
                      className="rounded-full border px-2 py-0.5 text-xs"
                    >
                      {statusLabel[employee.status]}
                    </span>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>

          {page && page.total_pages > 1 ? (
            <div className="flex items-center justify-end gap-2 text-sm">
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

      <EmployeeFormDialog open={dialogOpen} onOpenChange={setDialogOpen} employee={null} />
    </div>
  );
}
