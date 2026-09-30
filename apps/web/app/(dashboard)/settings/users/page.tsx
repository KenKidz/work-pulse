import type { Metadata } from "next";

import { PageHeader } from "@/components/page-header";
import { UsersTable } from "@/features/users/components/UsersTable";

export const metadata: Metadata = {
  title: "Users — WorkPulse",
};

export default function UsersSettingsPage() {
  return (
    <section className="space-y-6">
      <PageHeader
        title="Users"
        description="Accounts, roles and employee links — who can sign in, and as what."
      />
      <UsersTable />
    </section>
  );
}
