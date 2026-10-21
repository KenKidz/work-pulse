import type { Metadata } from "next";

import { PageHeader } from "@/components/page-header";
import { NotificationsList } from "@/features/notifications/components/NotificationsList";

export const metadata: Metadata = {
  title: "Notifications — WorkPulse",
};

export default function NotificationsPage() {
  return (
    <section className="space-y-6">
      <PageHeader
        title="Notifications"
        description="Leave and attendance updates addressed to you. Mark items read as you go."
      />
      <NotificationsList />
    </section>
  );
}
