import { ArrowUpRight } from "lucide-react";
import Link from "next/link";
import type { Metadata } from "next";

import { DESTINATIONS } from "@/components/destinations";
import { PageHeader } from "@/components/page-header";
import { Card, CardAction, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export const metadata: Metadata = {
  title: "Dashboard — WorkPulse",
};

export default function DashboardPage() {
  return (
    <section className="space-y-6">
      <PageHeader
        title="Dashboard"
        description="Your workforce at a glance — people records, live attendance and the organization behind them."
      />

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {DESTINATIONS.map((destination) => (
          <Link
            key={destination.href}
            href={destination.href}
            className="group rounded-xl outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
          >
            <Card className="h-full gap-3 py-5 transition-[border-color,box-shadow] group-hover:border-primary/40 group-hover:shadow-md">
              <CardHeader className="px-5">
                <span className="flex size-9 items-center justify-center rounded-lg bg-primary/10 text-primary">
                  <destination.icon className="size-4" aria-hidden="true" />
                </span>
                <CardTitle className="mt-3 text-base">{destination.label}</CardTitle>
                <CardDescription>{destination.summary}</CardDescription>
                <CardAction>
                  <ArrowUpRight
                    className="size-4 text-muted-foreground transition-colors group-hover:text-primary"
                    aria-hidden="true"
                  />
                </CardAction>
              </CardHeader>
            </Card>
          </Link>
        ))}
      </div>

      <p className="text-xs text-muted-foreground">
        Phase 1 is request/response — refresh or change the board date to update. Live push arrives
        with realtime in Phase 2.
      </p>
    </section>
  );
}
