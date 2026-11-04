import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { EmployeesWorkspace } from "@/features/employees/components/EmployeesWorkspace";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("employees");
  return { title: t("metadataTitle") };
}

export default async function EmployeesPage() {
  return <EmployeesWorkspace />;
}
