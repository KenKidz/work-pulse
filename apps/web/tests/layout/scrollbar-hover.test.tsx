/**
 * Scrollbar hover reveal — marks the scroller under the pointer or holding
 * keyboard focus so `globals.css` can show its thumb. jsdom has no layout, so
 * each fixture stubs the scroll metrics it would otherwise compute.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ScrollbarHover } from "@/components/scrollbar-hover";

function stubScrollable(element: HTMLElement) {
  Object.defineProperties(element, {
    clientHeight: { configurable: true, value: 100 },
    scrollHeight: { configurable: true, value: 400 },
    clientWidth: { configurable: true, value: 300 },
    scrollWidth: { configurable: true, value: 300 },
  });
  element.style.overflowY = "auto";
}

function Fixture() {
  return (
    <>
      <ScrollbarHover />
      <div data-testid="scroller" style={{ overflowY: "auto" }}>
        <button type="button">Row action</button>
      </div>
      <button type="button">Outside</button>
    </>
  );
}

describe("ScrollbarHover", () => {
  it("marks the scroller under the pointer and clears it on leave", () => {
    render(<Fixture />);
    const scroller = screen.getByTestId("scroller");
    stubScrollable(scroller);

    fireEvent.pointerOver(screen.getByRole("button", { name: "Row action" }));
    expect(scroller).toHaveAttribute("data-scrollbar-active");

    fireEvent.pointerOver(screen.getByRole("button", { name: "Outside" }));
    expect(scroller).not.toHaveAttribute("data-scrollbar-active");
  });

  it("keeps the marker while focus moves inside the scroller, then clears it", () => {
    render(<Fixture />);
    const scroller = screen.getByTestId("scroller");
    stubScrollable(scroller);
    const rowAction = screen.getByRole("button", { name: "Row action" });

    fireEvent.focusIn(rowAction);
    expect(scroller).toHaveAttribute("data-scrollbar-active");

    fireEvent.focusOut(rowAction, { relatedTarget: scroller });
    expect(scroller).toHaveAttribute("data-scrollbar-active");

    fireEvent.focusOut(rowAction, {
      relatedTarget: screen.getByRole("button", { name: "Outside" }),
    });
    expect(scroller).not.toHaveAttribute("data-scrollbar-active");
  });

  it("marks both document elements when the page itself scrolls", () => {
    const root = document.documentElement;
    Object.defineProperties(root, {
      clientHeight: { configurable: true, value: 500 },
      scrollHeight: { configurable: true, value: 1000 },
    });

    try {
      render(<Fixture />);
      fireEvent.pointerOver(screen.getByRole("button", { name: "Outside" }));

      expect(root).toHaveAttribute("data-scrollbar-active");
      expect(document.body).toHaveAttribute("data-scrollbar-active");
    } finally {
      Reflect.deleteProperty(root, "clientHeight");
      Reflect.deleteProperty(root, "scrollHeight");
    }
  });

  it("clears the marker when unmounted", () => {
    const { unmount } = render(<Fixture />);
    const scroller = screen.getByTestId("scroller");
    stubScrollable(scroller);

    fireEvent.pointerOver(screen.getByRole("button", { name: "Row action" }));
    expect(scroller).toHaveAttribute("data-scrollbar-active");

    unmount();
    expect(scroller).not.toHaveAttribute("data-scrollbar-active");
  });
});
