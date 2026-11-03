/**
 * Skip link for the dashboard shell — the first focusable element, visible
 * only while focused, jumping to `<main id="main">` past both the header and
 * the sidebar. The label is passed in from the server layout's translation.
 */

export function SkipToMain({ label }: { label: string }) {
  return (
    <a
      href="#main"
      className="sr-only rounded-md bg-background px-4 py-2 text-sm font-medium shadow-md outline-none focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:ring-[3px] focus:ring-ring/50"
    >
      {label}
    </a>
  );
}
