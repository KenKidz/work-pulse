import type { Metadata } from "next";

import { PageHeader } from "@/components/page-header";
import { MyPayslipsTable } from "@/features/payroll/components/MyPayslipsTable";

export const metadata: Metadata = {
  title: "My payslips — WorkPulse",
};

export default function MyPayslipsPage() {
  return (
    <section className="space-y-6">
      <PageHeader
        title="My payslips"
        description="Your simulated payroll payslips. Amounts are in your organization’s payroll currency; superseded payslips stay available for your records."
      />
      <MyPayslipsTable />
    </section>
  );
}
