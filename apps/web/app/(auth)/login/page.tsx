import type { Metadata } from "next";
import { Activity } from "lucide-react";
import { getTranslations } from "next-intl/server";

import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { LoginForm } from "@/features/auth/components/LoginForm";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("auth.metadata");
  return { title: t("loginTitle") };
}

/**
 * The pulse trace — the card's one signature moment. A thin EKG-style line
 * flush with the card's top edge draws itself once on load; under reduced
 * motion it lands instantly on the static drawn state (globals.css).
 * Decorative: hidden from the accessibility tree. Server Component.
 */
function PulseTrace() {
  return (
    <div className="text-primary/70" aria-hidden="true">
      <svg viewBox="0 0 320 28" preserveAspectRatio="none" className="block h-7 w-full" fill="none">
        <path
          d="M0 14 H96 L104 14 110 8 118 21 124 4 132 24 138 10 143 14 H320"
          strokeDasharray="1000"
          strokeDashoffset="1000"
          stroke="currentColor"
          strokeWidth={1.8}
          strokeLinecap="round"
          strokeLinejoin="round"
          vectorEffect="non-scaling-stroke"
          className="animate-[pulse-draw_1400ms_ease-out_both]"
        />
      </svg>
    </div>
  );
}

export default async function LoginPage() {
  const t = await getTranslations("auth.login");

  return (
    <Card className="gap-0 overflow-hidden py-0">
      <PulseTrace />
      <CardHeader className="pt-5 pb-6">
        <div className="flex items-center gap-2.5">
          <span
            className="flex size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground"
            aria-hidden="true"
          >
            <Activity className="size-4" />
          </span>
          <span className="font-semibold tracking-tight">WorkPulse</span>
        </div>
        <h1 className="text-2xl font-semibold tracking-tight">{t("title")}</h1>
        <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
      </CardHeader>
      <CardContent className="pb-6">
        <LoginForm />
      </CardContent>
    </Card>
  );
}
