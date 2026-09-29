import { Badge } from "@/components/ui/badge";

/**
 * Lifecycle chip shared by the departments / positions / locations tables so
 * the same status cannot render differently per tab.
 */
export function ArchiveStatusBadge({ archivedAt }: { archivedAt?: string | null }) {
  return archivedAt ? (
    <Badge variant="muted">Archived</Badge>
  ) : (
    <Badge variant="success">Active</Badge>
  );
}
