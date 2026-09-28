import type { Metadata } from "next";

import { EmployeeDetailCard } from "@/features/employees/components/EmployeeDetailCard";

export const metadata: Metadata = {
  title: "Employee — WorkPulse",
};

export default async function EmployeeDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <EmployeeDetailCard id={id} />;
}
