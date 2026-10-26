import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { PageHeader } from "@/components/page-header";
import { PayrollWorkspace } from "@/features/payroll/components/PayrollWorkspace";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("payroll");
  return { title: t("metadata.payrollTitle") };
}

export default async function PayrollPage() {
  const t = await getTranslations("payroll");

  return (
    <section className="space-y-6">
      <PageHeader
        title={t("page.payrollTitle")}
        description={t("page.payrollDescription")}
      />
      <PayrollWorkspace />
    </section>
  );
}
