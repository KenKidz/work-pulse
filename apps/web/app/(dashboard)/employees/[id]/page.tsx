import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { EmployeeDetailWorkspace } from "@/features/employees/components/EmployeeDetailWorkspace";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("employees");
  return { title: t("detailMetadataTitle") };
}

export default async function EmployeeDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <EmployeeDetailWorkspace id={id} />;
}
