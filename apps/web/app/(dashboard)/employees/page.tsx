import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { PageHeader } from "@/components/page-header";
import { EmployeesTable } from "@/features/employees/components/EmployeesTable";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("employees");
  return { title: t("metadataTitle") };
}

export default async function EmployeesPage() {
  const t = await getTranslations("employees");

  return (
    <section className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
      />
      <EmployeesTable />
    </section>
  );
}
