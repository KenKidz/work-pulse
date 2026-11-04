"use client";

import { useEffect } from "react";

const ACTIVE_ATTRIBUTE = "data-scrollbar-active";
const SCROLLABLE_OVERFLOW = new Set(["auto", "scroll", "overlay"]);

function viewportScrolls(): boolean {
  const root = document.documentElement;
  return root.scrollHeight > root.clientHeight + 1 || root.scrollWidth > root.clientWidth + 1;
}

/**
 * True when the element can actually scroll in at least one axis. Elements
 * whose content overflows but are not scroll containers (visible overflow)
 * must not be marked, so the computed overflow is checked too.
 */
function hasScrollableGeometry(element: HTMLElement): boolean {
  if (
    element.scrollHeight <= element.clientHeight + 1 &&
    element.scrollWidth <= element.clientWidth + 1
  ) {
    return false;
  }
  const style = getComputedStyle(element);
  return SCROLLABLE_OVERFLOW.has(style.overflowY) || SCROLLABLE_OVERFLOW.has(style.overflowX);
}

function scrollableAncestors(target: EventTarget | null): readonly HTMLElement[] {
  if (!(target instanceof Element)) return [];

  for (let element: Element | null = target; element; element = element.parentElement) {
    if (element === document.documentElement) {
      // Chromium styles the viewport scrollbar from <body>; other engines may
      // use <html>, so mark both while the page itself is the scroller.
      return viewportScrolls() ? [document.documentElement, document.body] : [];
    }
    if (element instanceof HTMLElement && hasScrollableGeometry(element)) return [element];
  }
  return [];
}

/**
 * Marks the scroll container under the pointer (or holding focus) with
 * `data-scrollbar-active`; `globals.css` reveals its thumb from that attribute.
 * Chromium does not repaint `::-webkit-scrollbar-thumb` when the scroller's own
 * `:hover` flips, so the state cannot live in a pure CSS selector. Mounted once
 * by the root providers; rendered output is intentionally empty.
 */
export function ScrollbarHover() {
  useEffect(() => {
    let active: readonly HTMLElement[] = [];

    const setActive = (next: readonly HTMLElement[]) => {
      if (
        next.length === active.length &&
        next.every((element, index) => element === active[index])
      ) {
        return;
      }
      for (const element of active) element.removeAttribute(ACTIVE_ATTRIBUTE);
      active = next;
      for (const element of active) element.setAttribute(ACTIVE_ATTRIBUTE, "");
    };

    const onPointerOver = (event: PointerEvent) => {
      setActive(scrollableAncestors(event.target));
    };

    const onPointerLeave = () => {
      setActive([]);
    };

    const onFocusIn = (event: FocusEvent) => {
      setActive(scrollableAncestors(event.target));
    };

    const onFocusOut = (event: FocusEvent) => {
      // Moving focus inside the same scroller must keep its thumb visible.
      if (
        event.relatedTarget instanceof Node &&
        active.some((element) => element.contains(event.relatedTarget as Node))
      ) {
        return;
      }
      setActive([]);
    };

    document.addEventListener("pointerover", onPointerOver, { passive: true });
    document.documentElement.addEventListener("pointerleave", onPointerLeave);
    document.addEventListener("focusin", onFocusIn);
    document.addEventListener("focusout", onFocusOut);

    return () => {
      document.removeEventListener("pointerover", onPointerOver);
      document.documentElement.removeEventListener("pointerleave", onPointerLeave);
      document.removeEventListener("focusin", onFocusIn);
      document.removeEventListener("focusout", onFocusOut);
      setActive([]);
    };
  }, []);

  return null;
}
