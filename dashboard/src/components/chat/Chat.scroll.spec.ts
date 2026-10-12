import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { parse } from "vue/compiler-sfc";
import ts from "typescript";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { effectScope, nextTick, reactive, ref, watch } from "vue";

// Chat's scroll controller lives inside script setup. Run its actual declarations
// with controlled geometry: happy-dom cannot lay out or anchor a scrolling panel.
const source = readFileSync(resolve("src/components/chat/Chat.vue"), "utf8");
const script = parse(source).descriptor.scriptSetup!.content;
const ast = ts.createSourceFile(
  "Chat.ts",
  script,
  ts.ScriptTarget.Latest,
  true,
);
const functions = new Set([
  "handleMessagesScrollIntent",
  "onBranchToggle",
  "endMessagesScrollGesture",
  "handleMessagesScroll",
  "loadOlderHistory",
  "captureReadingAnchor",
  "preserveReadingAnchor",
  "shouldStickToBottom",
  "setStickToBottom",
  "scrollToBottom",
  "recordViewportAnchor",
  "restoreSessionViewport",
  "runJumpLanding",
  "endJumpLanding",
  "beginJumpScrollLock",
  "endJumpScrollLock",
  "centerScrollTopFor",
  "scrollRowToCenter",
  "scrollToMessageIndex",
  "jumpToIndex",
]);
const variables = new Set([
  "messagesContainer",
  "branchHistoryCollapsed",
  "messagesListShell",
  "suppressHistoryAutoLoad",
  "jumpInProgress",
  "shouldStickToBottomBySession",
  "viewportAnchorBySession",
  "pendingViewportSessionId",
  "viewportSessionToken",
  "viewportInteractionToken",
  "bottomScrollFrame",
  "selfScrollTop",
  "lastMessagesScrollTop",
  "scrollGestureDirection",
  "readingViewportAnchor",
  "userScrollPending",
  "scrollPointerActive",
  "userScrollSettleTimer",
  "JUMP_SCROLL_SETTLE_MS",
  "JUMP_SCROLL_MAX_LOCK_MS",
  "jumpScrollSettleTimer",
  "jumpScrollCapTimer",
  "jumpScrollContainer",
  "jumpScrollOnScroll",
  "JUMP_GLIDE_MS",
  "JUMP_GLIDE_MIN_MS",
  "JUMP_LANDING_STABLE_MS",
  "JUMP_LANDING_MAX_MS",
  "JUMP_LANDING_CANCEL_EVENTS",
  "jumpLandingToken",
  "jumpLandingContainer",
  "jumpLandingOnGesture",
  "markerResizeObserver",
  "historyLayoutObserver",
]);
const declarations = ast.statements
  .filter((node) => {
    if (ts.isFunctionDeclaration(node)) return functions.has(node.name!.text);
    if (ts.isVariableStatement(node)) {
      return node.declarationList.declarations.every((declaration) =>
        variables.has(declaration.name.getText(ast)),
      );
    }
    if (
      ts.isExpressionStatement(node) &&
      ts.isCallExpression(node.expression)
    ) {
      const call = node.expression;
      return (
        call.expression.getText(ast) === "watch" &&
        (call.arguments[0].getText(ast).includes("messagesListShell") ||
          (call.arguments[0].getText(ast) === "currSessionId" &&
            call.arguments[2]?.getText(ast).includes('flush: "sync"')))
      );
    }
    return false;
  })
  .map((node) => node.getText(ast))
  .join("\n");
const code = ts.transpileModule(declarations, {
  compilerOptions: {
    target: ts.ScriptTarget.ES2022,
    module: ts.ModuleKind.None,
  },
}).outputText;

const cleanups: Array<() => void> = [];

/** Create an isolated controller with controllable scroll range and row geometry. */
function scrollHarness() {
  const scope = effectScope();
  const panel = document.createElement("section");
  const content = document.createElement("div");
  const row = document.createElement("div");
  row.className = "message-row";
  row.dataset.messageIndex = "0";
  content.appendChild(row);
  panel.appendChild(content);
  document.body.appendChild(panel);
  let height = 1600;
  let top = 1200;
  let rowTop = 1200;
  const writes: number[] = [];
  Object.defineProperties(panel, {
    scrollHeight: { get: () => height },
    clientHeight: { get: () => 400 },
    scrollTop: {
      get: () => top,
      set: (value: number) => {
        top = Math.max(0, Math.min(height - 400, value));
        writes.push(top);
      },
    },
  });
  panel.getBoundingClientRect = () => ({ top: 0, bottom: 400 }) as DOMRect;
  row.getBoundingClientRect = () =>
    ({
      top: rowTop - top,
      bottom: rowTop - top + 200,
      height: 200,
      width: 600,
    }) as DOMRect;
  const frames = new Map<number, FrameRequestCallback>();
  let frameId = 0;
  const observers: Array<{
    callback: ResizeObserverCallback;
    observe: ReturnType<typeof vi.fn>;
  }> = [];
  const mutations: Array<{ callback: MutationCallback }> = [];
  class Observer {
    observe = vi.fn();
    disconnect = vi.fn();
    constructor(callback: ResizeObserverCallback) {
      observers.push({ callback, observe: this.observe });
    }
  }
  const environment = {
    ref,
    watch,
    nextTick,
    props: reactive({ active: true }),
    currSessionId: ref("A"),
    historyPagingBySession: {} as Record<
      string,
      { hasMore: boolean; loadingOlder?: boolean }
    >,
    historyOffsetBySession: {} as Record<string, number>,
    threadSelection: { visible: false },
    loadOlderMessages: vi.fn(
      async (_id: string, options?: { beforePrepend?: () => void }) => {
        options?.beforePrepend?.();
      },
    ),
    isSessionRunning: vi.fn(() => false),
    hasLiveSystemRecord: vi.fn(() => false),
    updateScrollMarkers: vi.fn(),
    ResizeObserver: Observer,
    MutationObserver: class {
      observe = vi.fn();
      disconnect = vi.fn();
      constructor(callback: MutationCallback) {
        mutations.push({ callback });
      }
    },
    requestAnimationFrame: (callback: FrameRequestCallback) => {
      frames.set(++frameId, callback);
      return frameId;
    },
    cancelAnimationFrame: (id: number) => frames.delete(id),
  };
  const controller = scope.run(() =>
    new Function(
      ...Object.keys(environment),
      `${code}
    return {
      messagesContainer, messagesListShell, viewportAnchorBySession,
      scrollToBottom, handleMessagesScrollIntent, endMessagesScrollGesture,
      onBranchToggle,
      handleMessagesScroll, loadOlderHistory, restoreSessionViewport,
      setStickToBottom, shouldStickToBottom, jumpToIndex, runJumpLanding,
      endJumpLanding, endJumpScrollLock, jumpInProgress,
      getSessionToken: () => viewportSessionToken,
    };
  `,
    )(...Object.values(environment)),
  )!;
  controller.messagesContainer.value = panel;
  controller.messagesListShell.value = content;
  cleanups.push(() => {
    controller.endJumpLanding();
    controller.endJumpScrollLock();
    scope.stop();
    panel.remove();
  });
  return {
    ...environment,
    controller,
    panel,
    content,
    row,
    writes,
    observers,
    mutations,
    setLayout(values: { height?: number; top?: number; rowTop?: number }) {
      height = values.height ?? height;
      top = values.top ?? top;
      rowTop = values.rowTop ?? rowTop;
    },
    frame() {
      const pending = Array.from(frames.values());
      frames.clear();
      for (const callback of pending) callback(performance.now());
    },
  };
}

describe("Chat viewport ownership", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    for (const cleanup of cleanups.splice(0)) cleanup();
    vi.clearAllTimers();
    vi.useRealTimers();
  });

  it("coalesces stream and layout updates into one frame using the latest height", () => {
    const h = scrollHarness();
    h.controller.scrollToBottom();
    h.controller.scrollToBottom();
    h.setLayout({ height: 1900 });
    h.frame();
    expect(h.writes).toEqual([1500]);
  });

  it("yields to an upward wheel before the scroll event, even within 80px of bottom", () => {
    const h = scrollHarness();
    h.controller.scrollToBottom();
    h.controller.handleMessagesScrollIntent(
      new WheelEvent("wheel", { deltaY: -40 }),
    );
    h.setLayout({ top: 1160 });
    h.controller.handleMessagesScroll();
    expect(h.controller.shouldStickToBottom("A")).toBe(false);
    h.setLayout({ height: 1800 });
    h.controller.scrollToBottom();
    h.frame();
    h.controller.handleMessagesScroll();
    expect(h.panel.scrollTop).toBe(1160);
    expect(h.writes).toEqual([]);
    expect(h.controller.shouldStickToBottom("A")).toBe(false);
    h.controller.handleMessagesScrollIntent(
      new WheelEvent("wheel", { deltaY: 40 }),
    );
    h.setLayout({ top: 1400 });
    h.controller.handleMessagesScroll();
    expect(h.controller.shouldStickToBottom("A")).toBe(true);
  });

  it("pauses during a scrollbar drag and resumes when released at the bottom", () => {
    const h = scrollHarness();
    const event = new Event("pointerdown");
    Object.defineProperty(event, "target", { value: h.panel });
    h.controller.handleMessagesScrollIntent(event);
    h.controller.scrollToBottom();
    h.frame();
    expect(h.writes).toEqual([]);
    h.controller.endMessagesScrollGesture();
    expect(h.controller.shouldStickToBottom("A")).toBe(true);
  });

  it("keeps follow when shrinking content only clamps the idle viewport to bottom", () => {
    const h = scrollHarness();
    h.controller.scrollToBottom();
    h.frame();
    h.setLayout({ top: 1000, height: 1400 });
    h.controller.handleMessagesScroll();
    expect(h.controller.shouldStickToBottom("A")).toBe(true);
  });

  it("does not resume follow when shrinking content clamps an upward gesture to bottom", async () => {
    const h = scrollHarness();
    await nextTick();
    h.controller.scrollToBottom();
    h.frame();
    h.writes.length = 0;
    h.controller.handleMessagesScrollIntent(
      new WheelEvent("wheel", { deltaY: -40 }),
    );
    h.setLayout({ top: 1160, height: 1560 });
    h.controller.handleMessagesScroll();
    expect(h.controller.shouldStickToBottom("A")).toBe(false);
    h.setLayout({ height: 1900 });
    h.observers.at(-1)!.callback([], {} as ResizeObserver);
    h.frame();
    expect(h.panel.scrollTop).toBe(1160);
    expect(h.writes).toEqual([]);
  });

  it("yields to upward scrolls without an input event and resumes on a real downward return", async () => {
    const h = scrollHarness();
    await nextTick();
    h.controller.scrollToBottom();
    h.frame();
    h.writes.length = 0;
    h.setLayout({ top: 1000 });
    h.controller.handleMessagesScroll();
    expect(h.controller.shouldStickToBottom("A")).toBe(false);
    h.setLayout({ height: 1900 });
    h.observers.at(-1)!.callback([], {} as ResizeObserver);
    h.frame();
    expect(h.writes).toEqual([]);
    h.setLayout({ top: 1500 });
    h.controller.handleMessagesScroll();
    expect(h.controller.shouldStickToBottom("A")).toBe(true);
  });

  it("does not resume follow on releasing an upward drag after a layout clamp", async () => {
    const h = scrollHarness();
    await nextTick();
    h.controller.scrollToBottom();
    h.frame();
    h.writes.length = 0;
    const event = new Event("pointerdown");
    Object.defineProperty(event, "target", { value: h.panel });
    h.controller.handleMessagesScrollIntent(event);
    h.setLayout({ top: 1160 });
    h.controller.handleMessagesScroll();
    h.setLayout({ height: 1560 });
    h.controller.handleMessagesScroll();
    h.controller.endMessagesScrollGesture();
    expect(h.controller.shouldStickToBottom("A")).toBe(false);
    h.setLayout({ height: 1900 });
    h.observers.at(-1)!.callback([], {} as ResizeObserver);
    h.frame();
    expect(h.writes).toEqual([]);
  });

  it("resumes follow when a drag actually returns downward to bottom", () => {
    const h = scrollHarness();
    const event = new Event("pointerdown");
    Object.defineProperty(event, "target", { value: h.panel });
    h.controller.handleMessagesScrollIntent(event);
    h.setLayout({ top: 800 });
    h.controller.handleMessagesScroll();
    h.setLayout({ top: 1200 });
    h.controller.handleMessagesScroll();
    expect(h.controller.shouldStickToBottom("A")).toBe(false);
    h.controller.endMessagesScrollGesture();
    expect(h.controller.shouldStickToBottom("A")).toBe(true);
  });

  it("cancels a settling history landing on a native scroll without a gesture event", () => {
    const h = scrollHarness();
    h.controller.runJumpLanding(() => 600, false);
    h.frame();
    h.writes.length = 0;
    h.setLayout({ top: 500 });
    h.controller.handleMessagesScroll();
    h.frame();
    expect(h.panel.scrollTop).toBe(500);
    expect(h.writes).toEqual([]);
  });

  it("does not pull the reader back when expanding inherited history", async () => {
    const h = scrollHarness();
    await nextTick();
    h.controller.onBranchToggle(false);
    h.setLayout({ height: 2200, top: 1600 });
    h.observers.at(-1)!.callback([], {} as ResizeObserver);
    h.frame();
    expect(h.writes).toEqual([]);
    expect(h.controller.shouldStickToBottom("A")).toBe(false);
  });

  it("does not re-arm follow from the inherited divider's own compensation", async () => {
    const h = scrollHarness();
    h.controller.onBranchToggle(false);
    h.setLayout({ height: 1800, top: 1400 });
    await nextTick();
    h.controller.handleMessagesScroll();
    expect(h.controller.shouldStickToBottom("A")).toBe(false);
  });

  it("lets touch taps resume follow and scroll keys pause it without intercepting editing", () => {
    const h = scrollHarness();
    h.controller.handleMessagesScrollIntent(new Event("touchstart"));
    expect(h.controller.shouldStickToBottom("A")).toBe(false);
    h.controller.endMessagesScrollGesture();
    expect(h.controller.shouldStickToBottom("A")).toBe(true);
    const editing = new KeyboardEvent("keydown", { key: "ArrowUp" });
    Object.defineProperty(editing, "target", {
      value: document.createElement("textarea"),
    });
    h.controller.handleMessagesScrollIntent(editing);
    expect(h.controller.shouldStickToBottom("A")).toBe(true);
    h.controller.handleMessagesScrollIntent(
      new KeyboardEvent("keydown", { key: "ArrowUp" }),
    );
    expect(h.controller.shouldStickToBottom("A")).toBe(false);
  });

  it("observes async content height and follows only when the reader is following", async () => {
    const h = scrollHarness();
    await nextTick();
    const observer = h.observers.at(-1)!;
    expect(observer.observe).toHaveBeenCalledWith(h.content);
    h.setLayout({ height: 1900 });
    observer.callback([], {} as ResizeObserver);
    h.frame();
    expect(h.panel.scrollTop).toBe(1500);
    h.controller.setStickToBottom("A", false);
    h.setLayout({ height: 2200 });
    observer.callback([], {} as ResizeObserver);
    h.frame();
    expect(h.panel.scrollTop).toBe(1500);
  });

  it("preserves the existing row while excluding streamed tail growth from prepend compensation", async () => {
    const h = scrollHarness();
    h.setLayout({ top: 400, rowTop: 500 });
    h.controller.setStickToBottom("A", false);
    h.loadOlderMessages.mockImplementation(async (_id, options) => {
      // 200px prepended, 300px streamed at the tail, and 50px reader movement.
      h.setLayout({ top: 350 });
      options?.beforePrepend?.();
      h.setLayout({ height: 2100, rowTop: 700, top: 350 });
    });
    await h.controller.loadOlderHistory();
    expect(h.panel.scrollTop).toBe(550);
    h.controller.handleMessagesScroll();
    expect(h.controller.shouldStickToBottom("A")).toBe(false);
  });

  it("does not correct a prepend twice when the resize observer runs before paging completes", async () => {
    const h = scrollHarness();
    await nextTick();
    h.setLayout({ top: 400, rowTop: 500 });
    h.controller.setStickToBottom("A", false);
    h.loadOlderMessages.mockImplementation(async (_id, options) => {
      options?.beforePrepend?.();
      h.setLayout({ height: 2100, rowTop: 700 });
      h.observers.at(-1)!.callback([], {} as ResizeObserver);
    });
    await h.controller.loadOlderHistory();
    expect(h.panel.scrollTop).toBe(600);
    expect(h.writes).toEqual([600]);
  });

  it("preserves the reading position at the exact top where native anchoring is inactive", async () => {
    const h = scrollHarness();
    h.setLayout({ top: 0, rowTop: 0 });
    h.controller.setStickToBottom("A", false);
    h.loadOlderMessages.mockImplementation(async (_id, options) => {
      options?.beforePrepend?.();
      h.setLayout({ height: 2200, rowTop: 600 });
    });
    await h.controller.loadOlderHistory();
    expect(h.panel.scrollTop).toBe(600);
    expect(h.row.getBoundingClientRect().top).toBe(0);
  });

  it("preserves later growth and shrinkage above the reader while accounting for continued scrolling", async () => {
    const h = scrollHarness();
    await nextTick();
    h.setLayout({ top: 400, rowTop: 500 });
    h.controller.setStickToBottom("A", false);
    await h.controller.loadOlderHistory();
    h.setLayout({ height: 1900, rowTop: 800, top: 350 });
    h.observers.at(-1)!.callback([], {} as ResizeObserver);
    expect(h.panel.scrollTop).toBe(650);
    expect(h.row.getBoundingClientRect().top).toBe(150);
    h.setLayout({ height: 1700, rowTop: 600 });
    h.observers.at(-1)!.callback([], {} as ResizeObserver);
    expect(h.panel.scrollTop).toBe(450);
    expect(h.row.getBoundingClientRect().top).toBe(150);
  });

  it("preserves layout movement even when changes above and below cancel the total height", async () => {
    const h = scrollHarness();
    await nextTick();
    h.setLayout({ top: 400, rowTop: 500 });
    h.controller.setStickToBottom("A", false);
    await h.controller.loadOlderHistory();
    h.setLayout({ rowTop: 700 });
    h.mutations.at(-1)!.callback([], {} as MutationObserver);
    expect(h.panel.scrollTop).toBe(600);
    expect(h.row.getBoundingClientRect().top).toBe(100);
    h.controller.handleMessagesScroll();
    h.setLayout({ rowTop: 750 });
    h.mutations.at(-1)!.callback([], {} as MutationObserver);
    expect(h.panel.scrollTop).toBe(650);
    expect(h.row.getBoundingClientRect().top).toBe(100);
  });

  it("does not compensate the main panel for scrolling inside a code pane", async () => {
    const h = scrollHarness();
    await nextTick();
    h.setLayout({ top: 400, rowTop: 500 });
    h.controller.setStickToBottom("A", false);
    const pane = document.createElement("div");
    pane.style.overflowY = "auto";
    pane.textContent = "Code inside a separately scrolling pane";
    pane.getBoundingClientRect = h.row.getBoundingClientRect;
    h.row.appendChild(pane);
    const caret = document.createRange();
    caret.setStart(pane.firstChild!, 0);
    caret.setEnd(pane.firstChild!, 1);
    const original = Object.getOwnPropertyDescriptor(
      document,
      "caretRangeFromPoint",
    );
    Object.defineProperty(document, "caretRangeFromPoint", {
      configurable: true,
      value: () => caret,
    });
    try {
      await h.controller.loadOlderHistory();
      pane.scrollTop = 80;
      h.mutations.at(-1)!.callback([], {} as MutationObserver);
      expect(h.panel.scrollTop).toBe(400);
      expect(h.writes).toEqual([]);
    } finally {
      if (original)
        Object.defineProperty(document, "caretRangeFromPoint", original);
      else Reflect.deleteProperty(document, "caretRangeFromPoint");
    }
  });

  it("yields reading corrections to an explicit history jump and resumes when it finishes", async () => {
    const h = scrollHarness();
    await nextTick();
    h.setLayout({ top: 400, rowTop: 500 });
    h.controller.setStickToBottom("A", false);
    await h.controller.loadOlderHistory();
    h.controller.runJumpLanding(() => 450, false);
    h.frame();
    h.writes.length = 0;
    h.setLayout({ height: 1900, rowTop: 700 });
    h.observers.at(-1)!.callback([], {} as ResizeObserver);
    expect(h.writes).toEqual([]);
    h.controller.endJumpLanding();
    h.setLayout({ height: 2000, rowTop: 800 });
    h.observers.at(-1)!.callback([], {} as ResizeObserver);
    expect(h.panel.scrollTop).toBe(550);
  });

  it("rejects a paging completion after A -> B -> A instead of moving the fresh viewport", async () => {
    const h = scrollHarness();
    let complete!: () => void;
    h.loadOlderMessages.mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          complete = resolve;
        }),
    );
    const loading = h.controller.loadOlderHistory();
    h.currSessionId.value = "B";
    h.currSessionId.value = "A";
    h.setLayout({ top: 300, rowTop: 1600, height: 2400 });
    complete();
    await loading;
    expect(h.panel.scrollTop).toBe(300);
    expect(h.writes).toEqual([]);
  });

  it("rejects both another session's restore and an older generation of the same session", async () => {
    const h = scrollHarness();
    const token = h.controller.getSessionToken();
    h.controller.setStickToBottom("A", false);
    h.controller.viewportAnchorBySession.set("A", 0);
    h.currSessionId.value = "B";
    await h.controller.restoreSessionViewport("A", token);
    expect(h.controller.shouldStickToBottom("B")).toBe(true);
    h.currSessionId.value = "A";
    await h.controller.restoreSessionViewport("A", token);
    h.frame();
    expect(h.writes).toEqual([]);
  });

  it("cancels a history jump while paging and lets a new jump own the viewport", async () => {
    const h = scrollHarness();
    let complete!: () => void;
    h.historyOffsetBySession.A = 10;
    h.historyPagingBySession.A = { hasMore: true };
    h.loadOlderMessages.mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          complete = resolve;
        }),
    );
    const oldJump = h.controller.jumpToIndex(0, false);
    h.controller.handleMessagesScrollIntent(
      new WheelEvent("wheel", { deltaY: -40 }),
    );
    h.historyOffsetBySession.A = 0;
    expect(await h.controller.jumpToIndex(0, false)).toBe(true);
    complete();
    expect(await oldJump).toBe(false);
    h.frame();
    expect(h.writes).toHaveLength(1);
  });
});
