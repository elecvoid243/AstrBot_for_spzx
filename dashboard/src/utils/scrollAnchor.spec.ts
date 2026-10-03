// Unit tests for the scroll-anchoring helpers used when a collapsed/expanded
// block changes height (subagent think previews).
import { describe, expect, it, vi } from "vitest";

import { anchorBottom, findScrollableAncestor } from "./scrollAnchor";

function makeScrollable(): HTMLDivElement {
  const scroller = document.createElement("div");
  scroller.style.overflowY = "auto";
  return scroller;
}

describe("findScrollableAncestor", () => {
  it("returns the nearest overflow-y container", () => {
    const scroller = makeScrollable();
    const mid = document.createElement("div");
    const el = document.createElement("div");
    scroller.appendChild(mid);
    mid.appendChild(el);
    document.body.appendChild(scroller);
    try {
      expect(findScrollableAncestor(el)).toBe(scroller);
    } finally {
      scroller.remove();
    }
  });

  it("returns null when no scrollable ancestor exists", () => {
    const el = document.createElement("div");
    document.body.appendChild(el);
    try {
      expect(findScrollableAncestor(el)).toBeNull();
    } finally {
      el.remove();
    }
  });
});

describe("anchorBottom", () => {
  it("compensates scrollTop so the element bottom stays in place", () => {
    const scroller = makeScrollable();
    const el = document.createElement("div");
    scroller.appendChild(el);
    document.body.appendChild(scroller);
    // jsdom has no layout: track scrollTop through an accessor pair.
    let scrollTopValue = 300;
    Object.defineProperty(scroller, "scrollTop", {
      get: () => scrollTopValue,
      set: (value: number) => {
        scrollTopValue = value;
      },
      configurable: true,
    });
    vi.spyOn(el, "getBoundingClientRect").mockReturnValue({
      bottom: 120,
    } as DOMRect);
    try {
      // Bottom was at 400 before the collapse, now at 120: delta -280.
      anchorBottom(el, 400);
      expect(scrollTopValue).toBe(20);
    } finally {
      scroller.remove();
      vi.restoreAllMocks();
    }
  });

  it("leaves scrollTop untouched when the bottom did not move", () => {
    const scroller = makeScrollable();
    const el = document.createElement("div");
    scroller.appendChild(el);
    document.body.appendChild(scroller);
    let scrollTopValue = 250;
    Object.defineProperty(scroller, "scrollTop", {
      get: () => scrollTopValue,
      set: (value: number) => {
        scrollTopValue = value;
      },
      configurable: true,
    });
    vi.spyOn(el, "getBoundingClientRect").mockReturnValue({
      bottom: 400,
    } as DOMRect);
    try {
      anchorBottom(el, 400);
      expect(scrollTopValue).toBe(250);
    } finally {
      scroller.remove();
      vi.restoreAllMocks();
    }
  });
});
