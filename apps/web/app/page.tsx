import { Activity, ArrowRight } from "lucide-react";
import Link from "next/link";
import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { DESTINATIONS } from "@/components/destinations";
import { LocaleSwitcher } from "@/components/locale-switcher";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("metadata");
  return { title: t("homeTitle") };
}

export default async function HomePage() {
  const t = await getTranslations("home");
  const nav = await getTranslations("nav");

  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-4xl flex-col justify-center px-4 py-16">
      <div className="flex items-center gap-2">
        <span className="flex size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
          <Activity className="size-4" aria-hidden="true" />
        </span>
        <span className="font-semibold tracking-tight">WorkPulse</span>
        <div className="ml-auto">
          <LocaleSwitcher />
        </div>
      </div>

      <Badge variant="secondary" className="mt-8 w-fit">
        {t("badge")}
      </Badge>

      <h1 className="mt-4 text-4xl font-semibold tracking-tight text-balance">{t("title")}</h1>
      <p className="mt-3 max-w-2xl text-pretty text-muted-foreground">{t("description")}</p>

      <div className="mt-8 flex flex-wrap items-center gap-3">
        <Button asChild size="lg">
          <Link href="/dashboard">
            {t("openDashboard")}
            <ArrowRight aria-hidden="true" />
          </Link>
        </Button>
        <Button asChild variant="outline" size="lg">
          <Link href="/attendance">{t("viewLiveBoard")}</Link>
        </Button>
      </div>

      <div className="mt-12 grid gap-4 sm:grid-cols-3">
        {DESTINATIONS.map((destination) => (
          <Card key={destination.href} className="gap-3 py-5">
            <CardHeader className="px-5">
              <span className="flex size-8 items-center justify-center rounded-lg bg-secondary text-secondary-foreground">
                <destination.icon className="size-4" aria-hidden="true" />
              </span>
              <CardTitle className="mt-2 text-base">
                {nav(`destinations.${destination.key}.label`)}
              </CardTitle>
            </CardHeader>
            <CardContent className="px-5">
              <p className="text-sm text-pretty text-muted-foreground">
                {nav(`destinations.${destination.key}.blurb`)}
              </p>
            </CardContent>
          </Card>
        ))}
      </div>
    </main>
  );
}
