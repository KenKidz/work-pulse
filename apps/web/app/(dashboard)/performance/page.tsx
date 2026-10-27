import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { PageHeader } from "@/components/page-header";
import { PerformanceWorkspace } from "@/features/performance/components/PerformanceWorkspace";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("performance");
  return { title: t("metadataTitle") };
}

export default async function PerformancePage() {
  const t = await getTranslations("performance");

  return (
    <section className="space-y-6">
      <PageHeader title={t("pageTitle")} description={t("pageDescription")} />
      <PerformanceWorkspace />
    </section>
  );
}
