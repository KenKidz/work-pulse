import Link from "next/link";
import type { ReactNode } from "react";

export default function DashboardLayout({ children }: { children: ReactNode }) {
  return (
    <div className="mx-auto max-w-5xl px-4 py-8">
      <nav className="mb-6 border-b pb-4">
        <Link className="font-semibold tracking-tight" href="/dashboard">
          WorkPulse
        </Link>
      </nav>
      {children}
    </div>
  );
}
