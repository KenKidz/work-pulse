import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { DashboardWorkspace } from "@/features/dashboard/components/DashboardWorkspace";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("metadata");
  return { title: t("dashboardTitle") };
}

export default function DashboardPage() {
  return (
    <section className="space-y-6">
      <DashboardWorkspace />
    </section>
  );
}
