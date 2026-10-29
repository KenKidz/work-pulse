import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { PageHeader } from "@/components/page-header";
import { MyDocuments } from "@/features/documents/components/MyDocuments";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("documents");
  return { title: t("myDocuments.title") };
}

export default async function ProfilePage() {
  const t = await getTranslations("documents");

  return (
    <section className="space-y-6">
      <PageHeader title={t("myDocuments.title")} description={t("myDocuments.description")} />
      <MyDocuments />
    </section>
  );
}
