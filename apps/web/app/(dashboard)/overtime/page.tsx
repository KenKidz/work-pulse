import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { PageHeader } from "@/components/page-header";
import { HrVerificationQueue } from "@/features/overtime/components/HrVerificationQueue";
import { ManagerInbox } from "@/features/overtime/components/ManagerInbox";
import { MyOvertimeTable } from "@/features/overtime/components/MyOvertimeTable";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("overtime");
  return { title: t("metadataTitle") };
}

export default async function OvertimePage() {
  const t = await getTranslations("overtime");

  return (
    <section className="space-y-6">
      <PageHeader
        title={t("pageTitle")}
        description={t("pageDescription")}
      />
      <MyOvertimeTable />
      <ManagerInbox />
      <HrVerificationQueue />
    </section>
  );
}
