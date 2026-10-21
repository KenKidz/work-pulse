"use client";

/**
 * Employee display names for payroll rows. The wire carries identifiers only;
 * the employee list is readable by HR, managers and admins, never Finance.
 * The query is gated by role so a Finance session does not fire a request it
 * knows will be refused — the format helper falls back to the id tail.
 */

import { useListEmployees } from "@workpulse/api-client";

import { useAuth } from "@/features/auth/components/AuthGate";

import { EMPLOYEE_DIRECTORY_ROLES } from "../labels";

export function usePayrollDirectory() {
  const { user } = useAuth();
  const canRead = EMPLOYEE_DIRECTORY_ROLES.includes(user.role);
  const query = useListEmployees({ page_size: 100 }, { query: { enabled: canRead } });

  const employees = query.data?.status === 200 ? query.data.data.items : [];
  const nameById = new Map(
    employees.map((employee) => [
      employee.id,
      `${employee.first_name} ${employee.last_name}`.trim(),
    ]),
  );

  return { canRead, employees, nameById };
}
