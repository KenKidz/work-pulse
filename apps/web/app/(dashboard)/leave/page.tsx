import type { Metadata } from "next";

import { PageHeader } from "@/components/page-header";
import { LeaveBalanceCards } from "@/features/leave/components/LeaveBalanceCards";
import { MyRequestsTable } from "@/features/leave/components/MyRequestsTable";
import { TeamApprovals } from "@/features/leave/components/TeamApprovals";

export const metadata: Metadata = {
  title: "Leave — WorkPulse",
};

export default function LeavePage() {
  return (
    <section className="space-y-6">
      <PageHeader
        title="Leave"
        description="Request time off, follow approvals and see the days you have left."
      />
      <LeaveBalanceCards />
      <MyRequestsTable />
      <TeamApprovals />
    </section>
  );
}
