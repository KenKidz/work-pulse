import type { Metadata } from "next";

import { PageHeader } from "@/components/page-header";
import { ReportsWorkspace } from "@/features/reports/components/ReportsWorkspace";

export const metadata: Metadata = {
  title: "Reports — WorkPulse",
};

export default function ReportsPage() {
  return (
    <section className="space-y-6">
      <PageHeader
        title="Reports"
        description="Verified overtime, attendance, leave and headcount over time — projected from the workforce event stream, never read from the operational tables."
      />
      <ReportsWorkspace />
    </section>
  );
}
