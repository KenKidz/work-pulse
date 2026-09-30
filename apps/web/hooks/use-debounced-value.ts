import { useEffect, useState } from "react";

/**
 * Debounce a fast-changing value: the returned value updates only after
 * `delay` ms without a change (default 300 ms).
 *
 * Rule: filter, search and sort inputs that refetch a list go through this
 * hook — the debounced value feeds the query params, so typing costs one API
 * call per pause, never one per keystroke (`.ai/conventions.md`, TypeScript
 * section). Discrete controls (select, toggle, date picker) commit directly.
 */
export function useDebouncedValue<T>(value: T, delay = 300): T {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    const handle = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(handle);
  }, [value, delay]);

  return debounced;
}
