"use client";

/**
 * Role switch for the landing surface. Approvers get the operations board;
 * everyone else gets the self-service board. The API policy remains the
 * authority — each child only calls queries its role can read.
 */

import { useAuth } from "@/features/auth/components/AuthGate";
import { LEAVE_APPROVER_ROLES } from "@/features/leave/labels";

import { EmployeeDashboard } from "./EmployeeDashboard";
import { OperationsDashboard } from "./OperationsDashboard";

export function DashboardWorkspace() {
  const { user } = useAuth();
  return LEAVE_APPROVER_ROLES.includes(user.role) ? (
    <OperationsDashboard user={user} />
  ) : (
    <EmployeeDashboard user={user} />
  );
}
