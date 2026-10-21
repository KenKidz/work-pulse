import type { Metadata } from "next";

import { RunDetail } from "@/features/payroll/components/RunDetail";

export const metadata: Metadata = {
  title: "Payroll run — WorkPulse",
};

export default async function PayrollRunPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <RunDetail runId={id} />;
}
