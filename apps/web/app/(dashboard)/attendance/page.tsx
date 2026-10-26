import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { AttendanceBoard } from "@/features/attendance/components/AttendanceBoard";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("attendance");
  return { title: t("metadataTitle") };
}

export default function AttendancePage() {
  return (
    <section className="space-y-6">
      <AttendanceBoard />
    </section>
  );
}
