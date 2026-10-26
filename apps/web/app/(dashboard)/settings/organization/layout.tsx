import type { ReactNode } from "react";
import { getTranslations } from "next-intl/server";

import { PageHeader } from "@/components/page-header";
import { OrganizationNav } from "@/features/organizations/components/OrganizationNav";

export default async function OrganizationSettingsLayout({ children }: { children: ReactNode }) {
  const t = await getTranslations("organizations.settings");

  return (
    <section className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
      />
      <OrganizationNav />
      {children}
    </section>
  );
}
