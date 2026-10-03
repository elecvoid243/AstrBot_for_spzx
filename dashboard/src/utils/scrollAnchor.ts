// Scroll-position helpers for blocks whose height changes collapse/expand.
//
// Author: elecvoid243 | 2026-10-03

/**
 * Nearest ancestor that scrolls vertically, or null when none exists.
 *
 * @param el - Element to start the walk from (exclusive).
 * @returns The first ancestor whose computed `overflow-y` is auto or scroll.
 */
export function findScrollableAncestor(
  el: HTMLElement | null,
): HTMLElement | null {
  let node = el?.parentElement ?? null;
  while (node) {
    const overflowY = window.getComputedStyle(node).overflowY;
    if (overflowY === "auto" || overflowY === "scroll") return node;
    node = node.parentElement;
  }
  return null;
}

/**
 * Compensate the scroll position so `el`'s bottom edge stays where it was.
 *
 * Called after a collapse shrinks `el`: the nearest scrollable ancestor is
 * scrolled by the bottom-edge delta, keeping the line the user was looking
 * at (the collapse toggle) stationary in the viewport.
 *
 * @param el - The element that changed height.
 * @param beforeBottom - Its `getBoundingClientRect().bottom` captured before
 *   the change.
 */
export function anchorBottom(el: HTMLElement, beforeBottom: number): void {
  const scroller = findScrollableAncestor(el);
  if (!scroller) return;
  const delta = el.getBoundingClientRect().bottom - beforeBottom;
  if (delta) scroller.scrollTop += delta;
}
