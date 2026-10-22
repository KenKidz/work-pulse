import type { Metadata } from "next";

import { PageHeader } from "@/components/page-header";
import { HrVerificationQueue } from "@/features/overtime/components/HrVerificationQueue";
import { ManagerInbox } from "@/features/overtime/components/ManagerInbox";
import { MyOvertimeTable } from "@/features/overtime/components/MyOvertimeTable";

export const metadata: Metadata = {
  title: "Overtime — WorkPulse",
};

export default function OvertimePage() {
  return (
    <section className="space-y-6">
      <PageHeader
        title="Overtime"
        description="Claim hours worked beyond your shift, follow the manager and HR steps, and see what payroll counts."
      />
      <MyOvertimeTable />
      <ManagerInbox />
      <HrVerificationQueue />
    </section>
  );
}
