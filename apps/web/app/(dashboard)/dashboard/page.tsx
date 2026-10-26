import { ArrowUpRight } from "lucide-react";
import Link from "next/link";
import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { DESTINATIONS } from "@/components/destinations";
import { PageHeader } from "@/components/page-header";
import { Card, CardAction, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("metadata");
  return { title: t("dashboardTitle") };
}

export default async function DashboardPage() {
  const t = await getTranslations("dashboard");
  const nav = await getTranslations("nav");

  return (
    <section className="space-y-6">
      <PageHeader title={t("title")} description={t("description")} />

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
                <CardTitle className="mt-3 text-base">
                  {nav(`destinations.${destination.key}.label`)}
                </CardTitle>
                <CardDescription>{nav(`destinations.${destination.key}.summary`)}</CardDescription>
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

      <p className="text-xs text-muted-foreground">{t("phaseNote")}</p>
    </section>
  );
}
