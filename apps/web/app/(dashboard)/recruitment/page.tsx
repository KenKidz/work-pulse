import type { Metadata } from "next";

import { PageHeader } from "@/components/page-header";
import { RecruitmentWorkspace } from "@/features/recruitment/components/RecruitmentWorkspace";

export const metadata: Metadata = {
  title: "Recruitment — WorkPulse",
};

export default function RecruitmentPage() {
  return (
    <section className="space-y-6">
      <PageHeader
        title="Recruitment"
        description="Open jobs, move candidates through the pipeline and schedule interviews — hiring creates the employee record in one audited flow."
      />
      <RecruitmentWorkspace />
    </section>
  );
}
