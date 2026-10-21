import type { ReactNode } from "react";

type PayrollSectionHeaderProps = {
  /** The section's h2 id, mirrored by the wrapping `<section aria-labelledby>`. */
  id: string;
  title: string;
  description: string;
  /** Right-aligned actions (create buttons) that wrap below the copy on small screens. */
  children?: ReactNode;
};

/**
 * One header rhythm for the payroll workspace sections. The page header owns
 * the single h1; each panel gets a quiet h2 with a one-line explanation.
 */
export function PayrollSectionHeader({
  id,
  title,
  description,
  children,
}: PayrollSectionHeaderProps) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div className="space-y-1">
        <h2 id={id} className="text-lg leading-6 font-semibold tracking-tight">
          {title}
        </h2>
        <p className="max-w-2xl text-sm leading-5 text-pretty text-muted-foreground">
          {description}
        </p>
      </div>
      {children === undefined ? null : <div className="flex flex-wrap gap-2">{children}</div>}
    </div>
  );
}
