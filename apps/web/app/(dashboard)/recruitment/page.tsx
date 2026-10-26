import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { PageHeader } from "@/components/page-header";
import { RecruitmentWorkspace } from "@/features/recruitment/components/RecruitmentWorkspace";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("recruitment");
  return { title: t("metadataTitle") };
}

export default async function RecruitmentPage() {
  const t = await getTranslations("recruitment");

  return (
    <section className="space-y-6">
      <PageHeader
        title={t("pageTitle")}
        description={t("pageDescription")}
      />
      <RecruitmentWorkspace />
    </section>
  );
}
