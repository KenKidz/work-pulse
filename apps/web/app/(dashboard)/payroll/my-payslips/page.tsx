import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { PageHeader } from "@/components/page-header";
import { MyPayslipsTable } from "@/features/payroll/components/MyPayslipsTable";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("payroll");
  return { title: t("metadata.myPayslipsTitle") };
}

export default async function MyPayslipsPage() {
  const t = await getTranslations("payroll");

  return (
    <section className="space-y-6">
      <PageHeader
        title={t("page.myPayslipsTitle")}
        description={t("page.myPayslipsDescription")}
      />
      <MyPayslipsTable />
    </section>
  );
}
