import { useTranslations } from "next-intl";

import { Badge } from "@/components/ui/badge";

/**
 * Lifecycle chip shared by the departments / positions / locations tables so
 * the same status cannot render differently per tab.
 */
export function ArchiveStatusBadge({ archivedAt }: { archivedAt?: string | null }) {
  const t = useTranslations("organizations.status");

  return archivedAt ? (
    <Badge variant="muted">{t("archived")}</Badge>
  ) : (
    <Badge variant="success">{t("active")}</Badge>
  );
}
