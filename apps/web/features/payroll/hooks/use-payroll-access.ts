"use client";

/**
 * The web-side payroll gate. The workspace and a run's direct URL both use it:
 * a role without company-wide payroll visibility is redirected to the
 * dashboard (the API already answers `404`, so the UI only hides what it
 * cannot show). HR reaches the surface read-only; the mutation gate is
 * `canOperate`.
 */

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { useAuth } from "@/features/auth/components/AuthGate";

import { PAYROLL_OPERATOR_ROLES, PAYROLL_VIEW_ROLES } from "../labels";

export function usePayrollAccess() {
  const { user } = useAuth();
  const router = useRouter();
  const canView = PAYROLL_VIEW_ROLES.includes(user.role);
  const canOperate = PAYROLL_OPERATOR_ROLES.includes(user.role);

  useEffect(() => {
    if (!canView) {
      router.replace("/dashboard");
    }
  }, [canView, router]);

  return { user, canView, canOperate };
}
