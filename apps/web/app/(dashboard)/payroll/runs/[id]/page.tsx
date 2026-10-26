import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { RunDetail } from "@/features/payroll/components/RunDetail";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("payroll");
  return { title: t("metadata.runTitle") };
}

export default async function PayrollRunPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <RunDetail runId={id} />;
}
