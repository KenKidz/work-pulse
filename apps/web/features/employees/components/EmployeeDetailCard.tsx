"use client";

/**
 * Employee detail view — profile, current employment, full history and the
 * mutating actions (edit profile, transfer, termination). Transfer and
 * termination are disabled once the employee is terminated. Client leaf.
 */

import { ArrowLeft, Pencil, Repeat, UserMinus } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useDepartments } from "@/features/organizations/hooks/use-departments";
import { useLocations } from "@/features/organizations/hooks/use-locations";
import { usePositions } from "@/features/organizations/hooks/use-positions";

import { formatDate } from "./dates";
import { EmployeeFormDialog } from "./EmployeeFormDialog";
import { EmploymentHistory } from "./EmploymentHistory";
import { employmentTypeLabel, statusLabel } from "./labels";
import { TerminateDialog } from "./TerminateDialog";
import { TransferDialog } from "./TransferDialog";
import { useEmployee, useEmployees } from "../hooks/use-employees";
import type { EmployeeDetail } from "../types";

function useLookups() {
  // History can reference archived organization data, so lookups include it.
  const departmentsQuery = useDepartments({ include_archived: true, page_size: 100 });
  const positionsQuery = usePositions({ include_archived: true, page_size: 100 });
  const locationsQuery = useLocations({ include_archived: true, page_size: 100 });
  const colleaguesQuery = useEmployees({ page_size: 100 });

  const departments = departmentsQuery.data?.status === 200 ? departmentsQuery.data.data.items : [];
  const positions = positionsQuery.data?.status === 200 ? positionsQuery.data.data.items : [];
  const locations = locationsQuery.data?.status === 200 ? locationsQuery.data.data.items : [];
  const colleagues = colleaguesQuery.data?.status === 200 ? colleaguesQuery.data.data.items : [];

  return {
    departmentName: (id: string) =>
      departments.find((entry) => entry.id === id)?.name ?? "Unknown department",
    positionTitle: (id: string) =>
      positions.find((entry) => entry.id === id)?.title ?? "Unknown position",
    locationName: (id: string) =>
      locations.find((entry) => entry.id === id)?.name ?? "Unknown location",
    colleagueName: (id: string) => {
      const found = colleagues.find((entry) => entry.id === id);
      return found ? `${found.first_name} ${found.last_name}` : null;
    },
  };
}

function DetailRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-4 py-1.5 text-sm">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="text-right font-medium">{children}</dd>
    </div>
  );
}

export function EmployeeDetailCard({ id }: { id: string }) {
  const [editOpen, setEditOpen] = useState(false);
  const [transferOpen, setTransferOpen] = useState(false);
  const [terminateOpen, setTerminateOpen] = useState(false);

  const employeeQuery = useEmployee(id);
  const lookups = useLookups();

  if (employeeQuery.isPending) {
    return <p className="text-sm text-muted-foreground">Loading employee…</p>;
  }

  if (employeeQuery.isError) {
    return (
      <div role="alert" className="rounded-md border border-destructive/40 p-4 text-sm">
        <p>Could not load this employee. It may not exist.</p>
        <Button asChild variant="outline" size="sm" className="mt-3">
          <Link href="/employees">Back to employees</Link>
        </Button>
      </div>
    );
  }

  // The generated response type is a success|422 union; isError is excluded
  // above, so the payload is the 200 body.
  const employee = employeeQuery.data.data as EmployeeDetail;
  const terminated = employee.status === "terminated";
  const managerName = employee.manager_id
    ? (lookups.colleagueName(employee.manager_id) ?? "—")
    : "—";

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Button asChild variant="ghost" size="sm" className="mb-2 -ml-2">
            <Link href="/employees">
              <ArrowLeft />
              Employees
            </Link>
          </Button>
          <h1 className="text-2xl font-semibold tracking-tight">
            {employee.first_name} {employee.last_name}
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {employee.employee_code} · {statusLabel[employee.status]}
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" aria-label="Edit profile" onClick={() => setEditOpen(true)}>
            <Pencil />
            Edit profile
          </Button>
          <Button
            variant="outline"
            aria-label="Transfer employee"
            disabled={terminated}
            onClick={() => setTransferOpen(true)}
          >
            <Repeat />
            Transfer
          </Button>
          <Button
            variant="destructive"
            aria-label="Terminate employee"
            disabled={terminated}
            onClick={() => setTerminateOpen(true)}
          >
            <UserMinus />
            Terminate
          </Button>
        </div>
      </div>

      {terminated ? (
        <p className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
          This employee was terminated
          {employee.terminated_at ? ` on ${formatDate(employee.terminated_at)}` : ""}. Transfers and
          further termination are disabled.
        </p>
      ) : null}

      <div className="grid gap-6 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Profile</CardTitle>
          </CardHeader>
          <CardContent>
            <dl>
              <DetailRow label="Email">{employee.email}</DetailRow>
              <DetailRow label="Phone">{employee.phone ?? "—"}</DetailRow>
              <DetailRow label="Employment type">
                {employmentTypeLabel[employee.employment_type]}
              </DetailRow>
              <DetailRow label="Hired on">{formatDate(employee.hired_at)}</DetailRow>
              <DetailRow label="Manager">
                {employee.manager_id ? (
                  <Link className="hover:underline" href={`/employees/${employee.manager_id}`}>
                    {managerName}
                  </Link>
                ) : (
                  "—"
                )}
              </DetailRow>
            </dl>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Current assignment</CardTitle>
          </CardHeader>
          <CardContent>
            {employee.current_employment ? (
              <dl>
                <DetailRow label="Department">
                  {lookups.departmentName(employee.current_employment.department_id)}
                </DetailRow>
                <DetailRow label="Position">
                  {lookups.positionTitle(employee.current_employment.position_id)}
                </DetailRow>
                <DetailRow label="Location">
                  {lookups.locationName(employee.current_employment.location_id)}
                </DetailRow>
                <DetailRow label="Since">
                  {formatDate(employee.current_employment.effective_from)}
                </DetailRow>
              </dl>
            ) : (
              <p className="text-sm text-muted-foreground">
                No open employment period — the record is historical.
              </p>
            )}
          </CardContent>
        </Card>
      </div>

      <section>
        <h2 className="mb-3 text-lg font-semibold tracking-tight">Employment history</h2>
        <EmploymentHistory
          employments={employee.employments}
          departmentName={lookups.departmentName}
          positionTitle={lookups.positionTitle}
          locationName={lookups.locationName}
        />
      </section>

      <EmployeeFormDialog
        open={editOpen}
        onOpenChange={setEditOpen}
        employee={{
          id: employee.id,
          first_name: employee.first_name,
          last_name: employee.last_name,
          email: employee.email,
          phone: employee.phone,
        }}
      />
      {employee.current_employment ? (
        <TransferDialog
          open={transferOpen}
          onOpenChange={setTransferOpen}
          employeeId={employee.id}
          currentDepartmentId={employee.current_employment.department_id}
        />
      ) : null}
      <TerminateDialog
        open={terminateOpen}
        onOpenChange={setTerminateOpen}
        employeeId={employee.id}
      />
    </div>
  );
}
