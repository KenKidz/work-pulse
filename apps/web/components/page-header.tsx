type PageHeaderProps = {
  title: string;
  description?: string;
};

/**
 * Shared page header — one typographic rhythm for every dashboard surface.
 * Server-safe: no client hooks, so pages stay Server Components.
 */
export function PageHeader({ title, description }: PageHeaderProps) {
  return (
    <header className="flex flex-col gap-2">
      <h1 className="text-3xl leading-9 font-semibold tracking-tight text-balance">{title}</h1>
      {description ? (
        <p className="max-w-2xl text-sm leading-5 text-pretty text-muted-foreground">
          {description}
        </p>
      ) : null}
    </header>
  );
}
