import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { PageHeader } from "@/components/page-header";
import { LeaveBalanceCards } from "@/features/leave/components/LeaveBalanceCards";
import { MyRequestsTable } from "@/features/leave/components/MyRequestsTable";
import { TeamApprovals } from "@/features/leave/components/TeamApprovals";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("leave");
  return { title: t("metadataTitle") };
}

export default async function LeavePage() {
  const t = await getTranslations("leave");

  return (
    <section className="space-y-6">
      <PageHeader
        title={t("pageTitle")}
        description={t("pageDescription")}
      />
      <LeaveBalanceCards />
      <MyRequestsTable />
      <TeamApprovals />
    </section>
  );
}
