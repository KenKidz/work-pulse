import type { Metadata } from "next";

import { EmployeesTable } from "@/features/employees/components/EmployeesTable";

export const metadata: Metadata = {
  title: "Employees — WorkPulse",
};

export default function EmployeesPage() {
  return (
    <section className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Employees</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          The workforce record — who works where, in what role, since when.
        </p>
      </header>
      <EmployeesTable />
    </section>
  );
}
