import Link from "next/link";
import type { ReactNode } from "react";

export default function DashboardLayout({ children }: { children: ReactNode }) {
  return (
    <div className="container">
      <nav>
        <Link href="/dashboard">WorkPulse</Link>
      </nav>
      {children}
    </div>
  );
}