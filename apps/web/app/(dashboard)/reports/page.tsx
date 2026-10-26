import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { PageHeader } from "@/components/page-header";
import { ReportsWorkspace } from "@/features/reports/components/ReportsWorkspace";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("reports");
  return { title: t("metadataTitle") };
}

export default async function ReportsPage() {
  const t = await getTranslations("reports");

  return (
    <section className="space-y-6">
      <PageHeader
        title={t("pageTitle")}
        description={t("pageDescription")}
      />
      <ReportsWorkspace />
    </section>
  );
}
