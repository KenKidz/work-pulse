"use client";

/**
 * Employee detail shell — the existing record on Overview and the employee's
 * documents on a second tab. Tabs are the surface's only local state. The
 * Documents panel hides write controls from anyone without the manage
 * capability; the API stays the authority. Client leaf.
 */

import { useTranslations } from "next-intl";
import { useState } from "react";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { DocumentsPanel } from "@/features/documents/components/DocumentsPanel";

import { EmployeeDetailCard } from "./EmployeeDetailCard";

type EmployeeDetailTab = "overview" | "documents";

const TABS: EmployeeDetailTab[] = ["overview", "documents"];

export function EmployeeDetailWorkspace({ id }: { id: string }) {
  const t = useTranslations("employees.detail.tabs");
  const [tab, setTab] = useState<EmployeeDetailTab>("overview");

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
