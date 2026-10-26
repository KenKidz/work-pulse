"use client";

/**
 * The recruitment workspace — HR/Admin run Jobs, Candidates and Interviews;
 * an assigned interviewer (manager on the web) gets "My interviews" only.
 * Employees and Finance are redirected to the dashboard by the access guard.
 * Tabs are the surface's only local state (the Reports pattern). Client leaf.
 */

import { useState } from "react";

import { Spinner } from "@/components/ui/spinner";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import { useRecruitmentAccess } from "../hooks/use-recruitment";
import { CandidatesPanel } from "./CandidatesPanel";
import { InterviewsPanel } from "./InterviewsPanel";
import { JobsTable } from "./JobsTable";
import { MyInterviewsPanel } from "./MyInterviewsPanel";

type RecruitmentTab = "jobs" | "candidates" | "interviews" | "mine";

const MANAGEMENT_TABS: { value: RecruitmentTab; label: string }[] = [
  { value: "jobs", label: "Jobs" },
  { value: "candidates", label: "Candidates" },
  { value: "interviews", label: "Interviews" },
  { value: "mine", label: "My interviews" },
];

const INTERVIEWER_TABS: { value: RecruitmentTab; label: string }[] = [
  { value: "mine", label: "My interviews" },
];

export function RecruitmentWorkspace() {
  const { canView, canManage } = useRecruitmentAccess();
  const [tab, setTab] = useState<RecruitmentTab>(canManage ? "jobs" : "mine");

  if (!canView) {
    return (
      <div className="flex items-center justify-center gap-2 px-6 py-10 text-sm text-muted-foreground">
        <Spinner className="size-4" />
        <p>Checking access…</p>
      </div>
    );
  }

  const tabs = canManage ? MANAGEMENT_TABS : INTERVIEWER_TABS;

  return (
    <Tabs
      value={tab}
      onValueChange={(value) => setTab(value as RecruitmentTab)}
      className="gap-4"
      data-testid="recruitment-workspace"
    >
      <TabsList
        aria-label="Recruitment section"
        className="w-full justify-start overflow-x-auto sm:w-fit"
      >
        {tabs.map((item) => (
          <TabsTrigger key={item.value} value={item.value} className="px-3">
            {item.label}
          </TabsTrigger>
        ))}
      </TabsList>

      {canManage ? (
        <>
          <TabsContent value="jobs" className="mt-0">
            <JobsTable />
          </TabsContent>
          <TabsContent value="candidates" className="mt-0">
            <CandidatesPanel />
          </TabsContent>
          <TabsContent value="interviews" className="mt-0">
            <InterviewsPanel />
          </TabsContent>
          <TabsContent value="mine" className="mt-0">
            <MyInterviewsPanel />
          </TabsContent>
        </>
      ) : (
        <TabsContent value="mine" className="mt-0">
          <MyInterviewsPanel />
        </TabsContent>
      )}
    </Tabs>
  );
}
