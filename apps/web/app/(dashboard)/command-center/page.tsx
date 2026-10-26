import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { PageHeader } from "@/components/page-header";
import { CommandCenter } from "@/features/realtime/components/CommandCenter";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("realtime");
  return { title: t("metadataTitle") };
}

export default async function CommandCenterPage() {
  const t = await getTranslations("realtime");

  return (
    <section className="space-y-6">
      <PageHeader
        title={t("pageTitle")}
        description={t("pageDescription")}
      />
      <CommandCenter />
    </section>
  );
}
