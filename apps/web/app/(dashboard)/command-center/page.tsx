import type { Metadata } from "next";

import { PageHeader } from "@/components/page-header";
import { CommandCenter } from "@/features/realtime/components/CommandCenter";

export const metadata: Metadata = {
  title: "Command center — WorkPulse",
};

export default function CommandCenterPage() {
  return (
    <section className="space-y-6">
      <PageHeader
        title="Command center"
        description="Watch presence arrive in real time — who’s on site, who’s remote, who’s late, and every check-in and check-out in between."
      />
      <CommandCenter />
    </section>
  );
}
