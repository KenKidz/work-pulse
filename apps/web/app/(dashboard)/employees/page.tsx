import type { Metadata } from "next";

import { PageHeader } from "@/components/page-header";
import { EmployeesTable } from "@/features/employees/components/EmployeesTable";

export const metadata: Metadata = {
  title: "Employees — WorkPulse",
};

export default function EmployeesPage() {
  return (
    <section className="space-y-6">
      <PageHeader
        title="Employees"
        description="The workforce record — who works where, in what role, since when."
      />
      <EmployeesTable />
    </section>
  );
}
