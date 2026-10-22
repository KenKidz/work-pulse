import type { Metadata } from "next";

import { PayslipDetail } from "@/features/payroll/components/PayslipDetail";

export const metadata: Metadata = {
  title: "Payslip — WorkPulse",
};

export default async function MyPayslipPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <PayslipDetail payslipId={id} />;
}
