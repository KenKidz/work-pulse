"use client";

/**
 * Global keyboard shortcuts for shell chrome. One guard decides when a
 * keystroke belongs to a text control or an IME composition, so the sidebar
 * (⌘B) and the command palette (⌘K) cannot drift into different behavior.
 */

import { useEffect, useRef } from "react";

/** True when keystrokes belong to a control that must keep them. */
export function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
}

/**
 * Register a window-level shortcut. The handler and matcher are kept in refs
 * so re-renders never re-subscribe or fire stale closures.
 */
export function useHotkey(matches: (event: KeyboardEvent) => boolean, onTrigger: () => void): void {
  const matchesRef = useRef(matches);
  const triggerRef = useRef(onTrigger);

  // Keep the latest matcher/handler without re-subscribing the listener.
  useEffect(() => {
    matchesRef.current = matches;
    triggerRef.current = onTrigger;
  });

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.isComposing || isEditableTarget(event.target)) return;
      if (!matchesRef.current(event)) return;
      event.preventDefault();
      triggerRef.current();
    };

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);
}
