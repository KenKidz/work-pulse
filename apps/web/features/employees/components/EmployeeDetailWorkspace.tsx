"use client";

/**
 * Employee detail shell — the existing record on Overview and the employee's
 * documents on a second tab. Tabs are the surface's only local state. The
 * Documents panel hides write controls from anyone without the manage
 * capability; the API stays the authority. Client leaf.
 */

import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Spinner } from "@/components/ui/spinner";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAuth } from "@/features/auth/components/AuthGate";
import { DocumentsPanel } from "@/features/documents/components/DocumentsPanel";

import { EMPLOYEE_READ_ROLES } from "../roles";
import { EmployeeDetailCard } from "./EmployeeDetailCard";

type EmployeeDetailTab = "overview" | "documents";

const TABS: EmployeeDetailTab[] = ["overview", "documents"];

export function EmployeeDetailWorkspace({ id }: { id: string }) {
  const t = useTranslations("employees.detail.tabs");
  const root = useTranslations("employees");
  const router = useRouter();
  const { user } = useAuth();
  const [tab, setTab] = useState<EmployeeDetailTab>("overview");
  const allowed = EMPLOYEE_READ_ROLES.includes(user.role);

  useEffect(() => {
    // Direct visits by non-readers never render the record; the API would 403
    // for roles outside the directory policy anyway.
    if (!allowed) {
      router.replace("/dashboard");
    }
  }, [allowed, router]);

  if (!allowed) {
    return (
      <div className="flex items-center justify-center gap-2 px-6 py-10 text-sm text-muted-foreground">
        <Spinner className="size-4" />
        <p>{root("checkingAccess")}</p>
      </div>
    );
  }

  return (
    <Tabs
      value={tab}
      onValueChange={(value) => setTab(value as EmployeeDetailTab)}
      className="gap-4"
      data-testid="employee-detail-workspace"
    >
      <TabsList aria-label={t("aria")} className="w-full justify-start overflow-x-auto sm:w-fit">
        {TABS.map((item) => (
          <TabsTrigger key={item} value={item} className="px-3">
            {t(item)}
          </TabsTrigger>
        ))}
      </TabsList>

      <TabsContent value="overview" className="mt-0">
        <EmployeeDetailCard id={id} />
      </TabsContent>
      <TabsContent value="documents" className="mt-0">
        <DocumentsPanel key={id} employeeId={id} />
      </TabsContent>
    </Tabs>
  );
}
