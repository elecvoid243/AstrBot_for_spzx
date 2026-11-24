import {
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
  type Ref,
} from "vue";
import Sortable from "sortablejs";

export type BoardStage = "todo" | "doing" | "done";
let nextBoard = 0;

/** Let Sortable animate live layout, then hand the final order back to Vue before paint. */
export function useProjectBoardDrag(
  board: Ref<HTMLElement | undefined>,
  busy: Ref<boolean>,
  moveCard: (id: string, stage: BoardStage, before: string | null) => void,
  moveLanes: (order: BoardStage[]) => void,
) {
  const sorting = ref(false);
  const sortables: Sortable[] = [];
  let originalNext: ChildNode | null = null;
  let releaseBox: DOMRect | undefined;
  let outside = false;
  let cancelled = false;
  let landing: Animation | undefined;
  let frame = 0,
    previousTime = 0;
  let pointer: { x: number; y: number } | undefined;
  const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const speed = () => (motion.matches ? 0 : 220);

  function mirrorStyle(mirror: HTMLElement, source: HTMLElement) {
    const style = getComputedStyle(source);
    // A body-level mirror leaves the board's theme scope. Read before writing to
    // avoid repeatedly recalculating styles on a clone of a long column.
    const tokens = Array.from(style)
      .filter((key) => /^--(board-|lane-|v-theme-|sp-)/.test(key))
      .map((key) => [key, style.getPropertyValue(key)]);
    const typography = {
      fontFamily: style.fontFamily,
      fontSize: style.fontSize,
      lineHeight: style.lineHeight,
      color: style.color,
    };
    for (const [key, value] of tokens) mirror.style.setProperty(key, value);
    Object.assign(mirror.style, typography);
  }
  function trackPointer(e: MouseEvent | TouchEvent) {
    if (!sorting.value && e.type !== "pointerdown") return;
    const p = "touches" in e ? e.touches[0] : e;
    if (p) pointer = { x: p.clientX, y: p.clientY };
  }
  function edgeVelocity(point: number, low: number, high: number) {
    const band = Math.min(60, (high - low) / 3);
    if (band <= 0) return 0;
    const fromStart = Math.max(0, Math.min(1, 1 - (point - low) / band));
    const fromEnd = Math.max(0, Math.min(1, 1 - (high - point) / band));
    return 880 * (fromEnd * fromEnd - fromStart * fromStart);
  }
  function scrollFrame(now: number) {
    if (!sorting.value || !board.value) return;
    const host = board.value,
      bounds = host.getBoundingClientRect(),
      dt = Math.min(now - previousTime, 32) / 1000;
    previousTime = now;
    if (
      pointer &&
      pointer.x >= bounds.left - 8 &&
      pointer.x <= bounds.right + 8 &&
      pointer.y >= bounds.top - 8 &&
      pointer.y <= bounds.bottom + 8
    ) {
      if (host.scrollWidth > host.clientWidth)
        host.scrollLeft +=
          dt *
          edgeVelocity(
            pointer.x,
            Math.max(0, bounds.left),
            Math.min(innerWidth, bounds.right),
          );
      if (Sortable.dragged?.dataset.card) {
        const lane = document
          .elementFromPoint(pointer.x, pointer.y)
          ?.closest<HTMLElement>(".lane");
        const list =
          lane && host.contains(lane)
            ? lane.querySelector<HTMLElement>(".lane-cards")
            : null;
        if (list && list.scrollHeight > list.clientHeight) {
          const r = list.getBoundingClientRect();
          list.scrollTop +=
            dt *
            edgeVelocity(
              pointer.y,
              Math.max(0, r.top),
              Math.min(innerHeight, r.bottom),
            );
        }
      }
    }
    frame = requestAnimationFrame(scrollFrame);
  }
  function captureRelease(e: PointerEvent | TouchEvent | MouseEvent) {
    if (!sorting.value || !Sortable.ghost) return;
    const point = "changedTouches" in e ? e.changedTouches[0] : e;
    if (!point) return;
    releaseBox = Sortable.ghost.getBoundingClientRect();
    outside = !board.value?.contains(
      document.elementFromPoint(point.clientX, point.clientY),
    );
    cancelled ||= e.type.includes("cancel");
  }
  function keyboard(e: KeyboardEvent) {
    if (sorting.value && e.key === "Escape") {
      cancelled = true;
      // Complete the library's ordinary fallback cleanup, then restore Vue's original order.
      document.dispatchEvent(new MouseEvent("pointerup", { bubbles: true }));
      if (sorting.value)
        document.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    }
  }
  function updateMotion() {
    sortables.forEach((s) => s.option("animation", speed()));
  }
  onMounted(() => {
    const host = board.value!;
    const cardGroup = "project-board-" + ++nextBoard;
    const options: Sortable.Options = {
      animation: speed(),
      easing: "cubic-bezier(.2,.8,.2,1)",
      forceFallback: true,
      fallbackOnBody: true,
      fallbackTolerance: 5,
      delayOnTouchOnly: true,
      delay: 170,
      touchStartThreshold: 5,
      scroll: false,
      ghostClass: "board-drop-space",
      fallbackClass: "board-drag-mirror",
      onChoose: (e) => {
        originalNext = e.item.nextSibling;
      },
      onStart: (e) => {
        landing?.finish();
        sorting.value = true;
        previousTime = performance.now();
        frame = requestAnimationFrame(scrollFrame);
        outside = false;
        cancelled = false;
        releaseBox = undefined;
        if (Sortable.ghost) {
          mirrorStyle(Sortable.ghost, e.item);
          const list = Sortable.ghost.querySelector<HTMLElement>(".lane-cards");
          if (list)
            list.scrollTop =
              e.item.querySelector(".lane-cards")?.scrollTop ?? 0;
        }
      },
      onEnd: async (e) => {
        const card = e.item.dataset.card;
        const nextId =
          e.item.nextElementSibling?.getAttribute("data-card") ?? null;
        const stage = e.to.closest<HTMLElement>("[data-stage]")?.dataset
          .stage as BoardStage;
        const order = Array.from(
          host.children,
          (el) => (el as HTMLElement).dataset.stage as BoardStage,
        );
        const changed =
          e.from !== e.to || e.oldDraggableIndex !== e.newDraggableIndex;
        // Restore Vue's keyed DOM first. Both the restore and optimistic render occur in one frame.
        e.from.insertBefore(
          e.item,
          originalNext?.parentNode === e.from ? originalNext : null,
        );
        if (changed && !cancelled && !outside) {
          if (card) moveCard(card, stage, nextId);
          else moveLanes(order);
        }
        sorting.value = false;
        cancelAnimationFrame(frame);
        const from = releaseBox;
        await nextTick();
        const target = host.querySelector<HTMLElement>(
          card
            ? `[data-card="${CSS.escape(card)}"]`
            : `[data-stage="${e.item.dataset.stage}"]`,
        );
        if (!target || !from || !speed() || !host.isConnected) return;
        const to = target.getBoundingClientRect();
        if (!from.width || !to.width || !to.height) return;
        const copy = target.cloneNode(true) as HTMLElement;
        copy.setAttribute("aria-hidden", "true");
        copy.inert = true;
        mirrorStyle(copy, target);
        Object.assign(copy.style, {
          position: "fixed",
          left: to.left + "px",
          top: to.top + "px",
          width: to.width + "px",
          height: to.height + "px",
          maxHeight: "none",
          margin: "0",
          pointerEvents: "none",
          transformOrigin: "top left",
          zIndex: "2500",
          overflow: "hidden",
        });
        document.body.append(copy);
        const list = copy.querySelector<HTMLElement>(".lane-cards");
        if (list)
          list.scrollTop = target.querySelector(".lane-cards")?.scrollTop ?? 0;
        target.style.visibility = "hidden";
        landing = copy.animate(
          [
            {
              transform: `translate(${from.left - to.left}px,${
                from.top - to.top
              }px) scale(${from.width / to.width},${from.height / to.height})`,
            },
            { transform: "none" },
          ],
          { duration: 180, easing: "cubic-bezier(.2,.8,.2,1)" },
        );
        const cleanup = () => {
          copy.remove();
          target.style.removeProperty("visibility");
        };
        landing.onfinish = cleanup;
        landing.oncancel = cleanup;
      },
    };
    sortables.push(
      Sortable.create(host, {
        ...options,
        draggable: ".lane",
        handle: ".lane-heading",
        direction: "horizontal",
        filter: ".lane-add",
        preventOnFilter: false,
      }),
    );
    host.querySelectorAll<HTMLElement>(".lane-cards").forEach((el) =>
      sortables.push(
        Sortable.create(el, {
          ...options,
          group: cardGroup,
          draggable: ".todo-card",
          direction: "vertical",
          emptyInsertThreshold: 18,
        }),
      ),
    );
    for (const name of [
      "pointerup",
      "mouseup",
      "touchend",
      "pointercancel",
      "touchcancel",
    ] as const)
      document.addEventListener(name, captureRelease, true);
    host.addEventListener("pointerdown", trackPointer, true);
    document.addEventListener("pointermove", trackPointer, {
      capture: true,
      passive: true,
    });
    document.addEventListener("touchmove", trackPointer, {
      capture: true,
      passive: true,
    });
    document.addEventListener("keydown", keyboard, true);
    motion.addEventListener("change", updateMotion);
  });
  watch(busy, (value) => sortables.forEach((s) => s.option("disabled", value)));
  onBeforeUnmount(() => {
    cancelled = true;
    landing?.cancel();
    sortables.forEach((s) => s.destroy());
    for (const name of [
      "pointerup",
      "mouseup",
      "touchend",
      "pointercancel",
      "touchcancel",
    ] as const)
      document.removeEventListener(name, captureRelease, true);
    cancelAnimationFrame(frame);
    board.value?.removeEventListener("pointerdown", trackPointer, true);
    document.removeEventListener("pointermove", trackPointer, true);
    document.removeEventListener("touchmove", trackPointer, true);
    document.removeEventListener("keydown", keyboard, true);
    motion.removeEventListener("change", updateMotion);
  });
  return { sorting, motion };
}
