"use client";

import { Loader2Icon } from "lucide-react";
import { useTranslations } from "next-intl";
import { cn } from "cn";

function Spinner({ className, ...props }: React.ComponentProps<typeof Loader2Icon>) {
  const t = useTranslations("common.states");
  return (
    <Loader2Icon
      data-slot="spinner"
      role="status"
      aria-label={t("loading")}
      className={cn("size-4 animate-spin motion-reduce:animate-none", className)}
      {...props}
    />
  );
}

export { Spinner };
