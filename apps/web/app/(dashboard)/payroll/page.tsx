import type { Metadata } from "next";

import { PageHeader } from "@/components/page-header";
import { PayrollWorkspace } from "@/features/payroll/components/PayrollWorkspace";

export const metadata: Metadata = {
  title: "Payroll — WorkPulse",
};

export default function PayrollPage() {
  return (
    <section className="space-y-6">
      <PageHeader
        title="Payroll"
        description="Simulated payroll — configure pay inputs, run a closed month, inspect every line, then finalize."
      />
      <PayrollWorkspace />
    </section>
  );
}
