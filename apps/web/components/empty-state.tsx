import type { LucideIcon } from "lucide-react";

import { cn } from "cn";

type EmptyStateProps = {
  icon?: LucideIcon;
  /** The full, human sentence — callers keep the pinned feature copy. */
  message: string;
  className?: string;
};

/**
 * Dashed, quiet placeholder for lists that have nothing to show yet.
 * Purely presentational; the message stays a single text node so tests and
 * screen readers read it verbatim.
 */
export function EmptyState({ icon: Icon, message, className }: EmptyStateProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed px-6 py-10 text-center",
        className,
      )}
    >
      {Icon ? <Icon className="size-5 text-muted-foreground" aria-hidden="true" /> : null}
      <p className="text-sm text-pretty text-muted-foreground">{message}</p>
    </div>
  );
}
