import type { ReactNode } from "react";

import { PageHeader } from "@/components/page-header";
import { OrganizationNav } from "@/features/organizations/components/OrganizationNav";

export default function OrganizationSettingsLayout({ children }: { children: ReactNode }) {
  return (
    <section className="space-y-6">
      <PageHeader
        title="Organization"
        description="Departments, positions and locations your workforce hangs off."
      />
      <OrganizationNav />
      {children}
    </section>
  );
}
