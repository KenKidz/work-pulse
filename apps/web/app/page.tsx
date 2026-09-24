import Link from "next/link";

import { Button } from "@/components/ui/button";

export default function HomePage() {
  return (
    <main className="mx-auto max-w-3xl px-4 py-16">
      <h1 className="text-3xl font-semibold tracking-tight">WorkPulse</h1>
      <p className="mt-2 text-muted-foreground">
        Real-time workforce management and people analytics.
      </p>
      <p className="mt-1 text-sm text-muted-foreground">
        Phase 1 scaffold — the Employees and Attendance features land here next.
      </p>
      <Button asChild className="mt-6">
        <Link href="/dashboard">Open dashboard</Link>
      </Button>
    </main>
  );
}
