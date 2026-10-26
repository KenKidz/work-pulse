import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { PageHeader } from "@/components/page-header";
import { NotificationsList } from "@/features/notifications/components/NotificationsList";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("notifications");
  return { title: t("metadataTitle") };
}

export default async function NotificationsPage() {
  const t = await getTranslations("notifications");

  return (
    <section className="space-y-6">
      <PageHeader
        title={t("pageTitle")}
        description={t("pageDescription")}
      />
      <NotificationsList />
    </section>
  );
}
