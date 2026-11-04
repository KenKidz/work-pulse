import type { ReactNode } from "react";

type PageHeaderProps = {
  title: string;
  description?: string;
  /** Page-level actions, aligned with the title at `sm+` and stacked below it. */
  actions?: ReactNode;
};

/**
 * Shared page header — one typographic rhythm for every dashboard surface.
 * Server-safe: no client hooks, so pages stay Server Components. The `actions`
 * slot is a plain node, so a Server Component can pass a client action leaf
 * without making the header itself client-side.
 */
export function PageHeader({ title, description, actions }: PageHeaderProps) {
  return (
    <header className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
      <div className="flex min-w-0 flex-col gap-2">
        <h1 className="text-3xl leading-9 font-semibold tracking-tight text-balance">{title}</h1>
        {description ? (
          <p className="max-w-2xl text-sm leading-5 text-pretty text-muted-foreground">
            {description}
          </p>
        ) : null}
      </div>
      {actions ? <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
    </header>
  );
}
