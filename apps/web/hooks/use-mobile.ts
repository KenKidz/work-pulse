"use client";

/**
 * Viewport breakpoint shared by the shell. `useSyncExternalStore` keeps the
 * server snapshot deterministic (desktop) so the first client render matches
 * the server markup — the value settles after hydration via matchMedia.
 *
 * The `MediaQueryList` is created lazily once and the subscribe/snapshot
 * callbacks are module-stable, so re-renders never churn the listener.
 */

import { useSyncExternalStore } from "react";

const MOBILE_BREAKPOINT = 768;
const MOBILE_QUERY = `(max-width: ${MOBILE_BREAKPOINT - 1}px)`;

let mobileQueryList: MediaQueryList | null = null;

function getMediaQueryList(): MediaQueryList {
  mobileQueryList ??= window.matchMedia(MOBILE_QUERY);
  return mobileQueryList;
}

function subscribe(callback: () => void): () => void {
  const queryList = getMediaQueryList();
  queryList.addEventListener("change", callback);
  return () => queryList.removeEventListener("change", callback);
}

function getSnapshot(): boolean {
  return getMediaQueryList().matches;
}

function getServerSnapshot(): boolean {
  return false;
}

export function useIsMobile(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
