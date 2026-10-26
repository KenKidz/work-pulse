import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { PayslipDetail } from "@/features/payroll/components/PayslipDetail";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("payroll");
  return { title: t("metadata.payslipTitle") };
}

export default async function MyPayslipPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <PayslipDetail payslipId={id} />;
}
