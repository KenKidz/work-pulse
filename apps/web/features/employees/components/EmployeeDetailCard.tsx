"use client";

/**
 * Employee detail view — profile, current employment, full history and the
 * mutating actions (edit profile, transfer, termination). Transfer and
 * termination are disabled once the employee is terminated. Client leaf.
 */

import { ArrowLeft, CircleAlert, Pencil, Repeat, UserMinus } from "lucide-react";
import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";
import { useDepartments } from "@/features/organizations/hooks/use-departments";
import { useLocations } from "@/features/organizations/hooks/use-locations";
import { usePositions } from "@/features/organizations/hooks/use-positions";

import { formatDate } from "./dates";
import { EmployeeFormDialog } from "./EmployeeFormDialog";
import { EmploymentHistory } from "./EmploymentHistory";
import { statusVariant } from "./labels";
import { TerminateDialog } from "./TerminateDialog";
import { TransferDialog } from "./TransferDialog";
import { useEmployee, useEmployees } from "../hooks/use-employees";
import type { EmployeeDetail } from "../types";

function useLookups(t: ReturnType<typeof useTranslations<"employees">>) {
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
      departments.find((entry) => entry.id === id)?.name ?? t("detail.unknownDepartment"),
    positionTitle: (id: string) =>
      positions.find((entry) => entry.id === id)?.title ?? t("detail.unknownPosition"),
    locationName: (id: string) =>
      locations.find((entry) => entry.id === id)?.name ?? t("detail.unknownLocation"),
    colleagueName: (id: string) => {
      const found = colleagues.find((entry) => entry.id === id);
      return found ? `${found.first_name} ${found.last_name}` : null;
    },
  };
}

function DetailRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-4 py-2 text-sm">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="text-right font-medium">{children}</dd>
    </div>
  );
}

export function EmployeeDetailCard({ id }: { id: string }) {
  const t = useTranslations("employees");
  const locale = useLocale();
  const [editOpen, setEditOpen] = useState(false);
  const [transferOpen, setTransferOpen] = useState(false);
  const [terminateOpen, setTerminateOpen] = useState(false);

  const employeeQuery = useEmployee(id);
  const lookups = useLookups(t);

  if (employeeQuery.isPending) {
    return (
      <div className="flex items-center justify-center gap-2 rounded-lg border border-dashed px-6 py-10 text-sm text-muted-foreground">
        <Spinner className="size-4" />
        <p>{t("detail.loading")}</p>
      </div>
    );
  }

  if (employeeQuery.isError) {
    return (
      <div className="space-y-4">
        <Alert variant="destructive">
          <CircleAlert aria-hidden="true" />
          <AlertDescription>
            <p>{t("detail.loadError")}</p>
          </AlertDescription>
        </Alert>
        <Button asChild variant="outline" size="sm">
          <Link href="/employees">{t("detail.backToEmployees")}</Link>
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
  const initials = `${employee.first_name.charAt(0)}${employee.last_name.charAt(0)}`.toUpperCase();

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <Button asChild variant="ghost" size="sm" className="mb-2 -ml-2">
            <Link href="/employees">
              <ArrowLeft />
              Employees
            </Link>
          </Button>
          <div className="flex items-center gap-3">
            <div
              aria-hidden="true"
              className="flex size-12 shrink-0 items-center justify-center rounded-full bg-primary/10 text-base font-semibold text-primary"
            >
              {initials}
            </div>
            <div className="min-w-0">
              <h1 className="text-2xl font-semibold tracking-tight text-balance">
                {employee.first_name} {employee.last_name}
              </h1>
              <p className="mt-1.5 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
                <span className="font-mono text-xs">{employee.employee_code}</span>
                <Badge variant={statusVariant[employee.status]}>
                  {t(`status.${employee.status}`)}
                </Badge>
              </p>
            </div>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" aria-label={t("detail.editProfileAria")} onClick={() => setEditOpen(true)}>
            <Pencil />
            {t("detail.editProfile")}
          </Button>
          <Button
            variant="outline"
            aria-label={t("detail.transferAria")}
            disabled={terminated}
            onClick={() => setTransferOpen(true)}
          >
            <Repeat />
            {t("detail.transfer")}
          </Button>
          <Button
            variant="destructive"
            aria-label={t("detail.terminateAria")}
            disabled={terminated}
            onClick={() => setTerminateOpen(true)}
          >
            <UserMinus />
            {t("detail.terminate")}
          </Button>
        </div>
      </div>

      {terminated ? (
        <Alert variant="destructive">
          <AlertDescription>
            <p>
              {employee.terminated_at
                ? t("detail.terminatedOn", { date: formatDate(employee.terminated_at, locale) })
                : t("detail.terminated")}
            </p>
          </AlertDescription>
        </Alert>
      ) : null}

      <div className="grid gap-6 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>{t("detail.profile")}</CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="divide-y">
              <DetailRow label={t("detail.email")}>{employee.email}</DetailRow>
              <DetailRow label={t("detail.phone")}>{employee.phone ?? "—"}</DetailRow>
              <DetailRow label={t("detail.employmentType")}>
                {t(`types.${employee.employment_type}`)}
              </DetailRow>
              <DetailRow label={t("detail.hiredOn")}>{formatDate(employee.hired_at, locale)}</DetailRow>
              <DetailRow label={t("detail.manager")}>
                {employee.manager_id ? (
                  <Link
                    className="underline-offset-4 hover:underline"
                    href={`/employees/${employee.manager_id}`}
                  >
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
            <CardTitle>{t("detail.currentAssignment")}</CardTitle>
          </CardHeader>
          <CardContent>
            {employee.current_employment ? (
              <dl className="divide-y">
                <DetailRow label={t("detail.department")}>
                  {lookups.departmentName(employee.current_employment.department_id)}
                </DetailRow>
                <DetailRow label={t("detail.position")}>
                  {lookups.positionTitle(employee.current_employment.position_id)}
                </DetailRow>
                <DetailRow label={t("detail.location")}>
                  {lookups.locationName(employee.current_employment.location_id)}
                </DetailRow>
                <DetailRow label={t("detail.since")}>
                  {formatDate(employee.current_employment.effective_from, locale)}
                </DetailRow>
              </dl>
            ) : (
              <p className="text-sm text-muted-foreground">{t("detail.noOpenPeriod")}</p>
            )}
          </CardContent>
        </Card>
      </div>

      <section>
        <h2 className="mb-3 text-lg font-semibold tracking-tight">{t("detail.history")}</h2>
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
