"use client";

/**
 * The performance workspace — every role reaches their own Goals, Reviews and
 * Skills; Manager/HR/Admin additionally see the team or tenant scope. The
 * capability matrix decides which controls the panels render (the API stays
 * the authority). A role with neither a tenant scope nor an employee link has
 * nothing to show and gets the denied state instead of empty tabs. Tabs are
 * the surface's only local state (the recruitment/reports pattern). Client
 * leaf.
 */

import { useTranslations } from "next-intl";
import { useState } from "react";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import { usePerformanceAccess } from "../hooks/use-performance";
import { GoalsPanel } from "./GoalsPanel";
import { PerformanceDeniedState } from "./PerformanceDeniedState";
import { ReviewsPanel } from "./ReviewsPanel";
import { SkillsPanel } from "./SkillsPanel";

type PerformanceTab = "goals" | "reviews" | "skills";

const TABS: PerformanceTab[] = ["goals", "reviews", "skills"];

export function PerformanceWorkspace() {
  const t = useTranslations("performance");
  const { canUseWorkspace } = usePerformanceAccess();
  const [tab, setTab] = useState<PerformanceTab>("goals");

  if (!canUseWorkspace) {
    return <PerformanceDeniedState message={t("workspace.noEmployeeLink")} />;
  }

  return (
    <Tabs
      value={tab}
      onValueChange={(value) => setTab(value as PerformanceTab)}
      className="gap-4"
      data-testid="performance-workspace"
    >
      <TabsList
        aria-label={t("tabsAria")}
        className="w-full justify-start overflow-x-auto sm:w-fit"
      >
        {TABS.map((item) => (
          <TabsTrigger key={item} value={item} className="px-3">
            {t(`tabs.${item}`)}
          </TabsTrigger>
        ))}
      </TabsList>

      <TabsContent value="goals" className="mt-0">
        <GoalsPanel />
      </TabsContent>
      <TabsContent value="reviews" className="mt-0">
        <ReviewsPanel />
      </TabsContent>
      <TabsContent value="skills" className="mt-0">
        <SkillsPanel />
      </TabsContent>
    </Tabs>
  );
}
