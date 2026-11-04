"use client";

/**
 * Employees workspace — the directory is manager+ only (mirrors the API's
 * `READ_ROLES`): managers see their direct reports, HR/Admin/Super Admin the
 * tenant. Everyone else is redirected to the dashboard before the list
 * queries mount, so an employee session never sees the 403 error surface.
 * The create action is HR/Admin/Super Admin only. Client leaf.
 */

import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { useEffect } from "react";

import { PageHeader } from "@/components/page-header";
import { Spinner } from "@/components/ui/spinner";
import { useAuth } from "@/features/auth/components/AuthGate";

import { EMPLOYEE_MANAGE_ROLES, EMPLOYEE_READ_ROLES } from "../roles";
import { EmployeesTable } from "./EmployeesTable";
import { NewEmployeeButton } from "./NewEmployeeButton";

export function EmployeesWorkspace() {
  const t = useTranslations("employees");
  const router = useRouter();
  const { user } = useAuth();
  const allowed = EMPLOYEE_READ_ROLES.includes(user.role);

  useEffect(() => {
    // Direct visits by non-readers never render the directory; the employees
    // API would 403 anyway. The query tree only mounts once `allowed` is true.
    if (!allowed) {
      router.replace("/dashboard");
    }
  }, [allowed, router]);

  if (!allowed) {
    return (
      <section className="space-y-6">
        <PageHeader title={t("title")} description={t("description")} />
        <div className="flex items-center justify-center gap-2 px-6 py-10 text-sm text-muted-foreground">
          <Spinner className="size-4" />
          <p>{t("checkingAccess")}</p>
        </div>
      </section>
    );
  }

  return (
    <section className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={EMPLOYEE_MANAGE_ROLES.includes(user.role) ? <NewEmployeeButton /> : undefined}
      />
      <EmployeesTable />
    </section>
  );
}
