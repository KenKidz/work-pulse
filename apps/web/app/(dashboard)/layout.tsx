import Link from "next/link";
import type { ReactNode } from "react";

export default function DashboardLayout({ children }: { children: ReactNode }) {
  return (
    <div className="mx-auto max-w-5xl px-4 py-8">
      <nav className="mb-6 flex items-center gap-6 border-b pb-4">
        <Link className="font-semibold tracking-tight" href="/dashboard">
          WorkPulse
        </Link>
        <Link className="text-sm text-muted-foreground hover:text-foreground" href="/employees">
          Employees
        </Link>
        <Link className="text-sm text-muted-foreground hover:text-foreground" href="/attendance">
          Attendance
        </Link>
        <Link
          className="text-sm text-muted-foreground hover:text-foreground"
          href="/settings/organization"
        >
          Organization
        </Link>
      </nav>
      {children}
    </div>
  );
}
