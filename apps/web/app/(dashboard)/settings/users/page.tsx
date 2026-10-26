import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { PageHeader } from "@/components/page-header";
import { UsersTable } from "@/features/users/components/UsersTable";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("users");
  return { title: t("metadataTitle") };
}

export default async function UsersSettingsPage() {
  const t = await getTranslations("users");

  return (
    <section className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
      />
      <UsersTable />
    </section>
  );
}
