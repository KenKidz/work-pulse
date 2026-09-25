import type { ReactNode } from "react";

import { OrganizationNav } from "@/features/organizations/components/OrganizationNav";

export default function OrganizationSettingsLayout({ children }: { children: ReactNode }) {
  return (
    <section className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Organization</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Departments, positions and locations your workforce hangs off.
        </p>
      </header>
      <OrganizationNav />
      {children}
    </section>
  );
}
