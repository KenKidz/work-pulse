import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Vitest runs without `globals: true`, so Testing Library cannot register its
// automatic DOM cleanup — do it explicitly between tests.
afterEach(() => {
  cleanup();
});

// jsdom gaps that Radix primitives (dialog, select) rely on in feature tests.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

if (!("ResizeObserver" in globalThis)) {
  (globalThis as Record<string, unknown>).ResizeObserver = ResizeObserverStub;
}

// jsdom does not implement matchMedia — the shell's `useIsMobile` subscribes
// to it. Default to desktop; mobile tests mock `@/hooks/use-mobile` directly.
if (!("matchMedia" in window)) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    configurable: true,
    value: (query: string): MediaQueryList =>
      ({
        matches: false,
        media: query,
        onchange: null,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
        addListener: () => undefined,
        removeListener: () => undefined,
        dispatchEvent: () => false,
      }) as unknown as MediaQueryList,
  });
}

for (const method of [
  "hasPointerCapture",
  "setPointerCapture",
  "releasePointerCapture",
  "scrollIntoView",
] as const) {
  if (!(method in Element.prototype)) {
    Object.defineProperty(Element.prototype, method, {
      value: () => undefined,
      writable: true,
      configurable: true,
    });
  }
}
