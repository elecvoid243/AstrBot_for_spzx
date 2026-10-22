// Author: elecvoid243 @ 2026-10-22
// The side-chat lifecycle lives inside Chat.vue's script setup, so run its
// actual declarations with controlled refs (same technique as Chat.scroll).
// Focus: closing the panel only hides it now — side chats persist and are
// deleted explicitly from the side-chat menu.

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { parse } from "vue/compiler-sfc";
import ts from "typescript";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ref, type Ref } from "vue";

const script = parse(
  readFileSync(resolve("src/components/chat/Chat.vue"), "utf8"),
).descriptor.scriptSetup!.content;
const ast = ts.createSourceFile("Chat.ts", script, ts.ScriptTarget.Latest, true);
const wanted = new Set(["closeThreadPanel", "onThreadPanelToggle"]);
const declarations = ast.statements.filter(
  (node): node is ts.FunctionDeclaration =>
    ts.isFunctionDeclaration(node) &&
    Boolean(node.name) &&
    wanted.has(node.name!.text),
);
const code = declarations
  .map((node) =>
    ts.transpileModule(node.getText(ast), {
      compilerOptions: { target: ts.ScriptTarget.ES2022 },
    }).outputText,
  )
  .join("\n");

interface ThreadRef {
  thread_id: string;
}

function setup() {
  const activeThread: Ref<ThreadRef | null> = ref(null);
  const sideChatThreadId: Ref<string | null> = ref(null);
  const threadPanelOpen = ref(true);
  const api = new Function(
    "activeThread",
    "sideChatThreadId",
    "threadPanelOpen",
    `${code}
     return { closeThreadPanel, onThreadPanelToggle };`,
  )(activeThread, sideChatThreadId, threadPanelOpen) as {
    closeThreadPanel: () => void;
    onThreadPanelToggle: (open: boolean) => void;
  };
  return {
    activeThread,
    sideChatThreadId,
    threadPanelOpen,
    ...api,
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("Chat side-chat lifecycle", () => {
  it("closes a branch thread and clears the panel state", () => {
    const ctx = setup();
    ctx.activeThread.value = { thread_id: "branch-1" };

    ctx.closeThreadPanel();

    expect(ctx.threadPanelOpen.value).toBe(false);
    expect(ctx.activeThread.value).toBeNull();
    expect(ctx.sideChatThreadId.value).toBeNull();
  });

  it("closes a side chat without deleting the backend thread", () => {
    const ctx = setup();
    ctx.activeThread.value = { thread_id: "side-1" };
    ctx.sideChatThreadId.value = "side-1";

    ctx.closeThreadPanel();

    expect(ctx.threadPanelOpen.value).toBe(false);
    expect(ctx.activeThread.value).toBeNull();
    expect(ctx.sideChatThreadId.value).toBeNull();
  });

  it("routes ThreadPanel's close event through the same teardown", () => {
    const ctx = setup();
    ctx.activeThread.value = { thread_id: "side-3" };
    ctx.sideChatThreadId.value = "side-3";

    ctx.onThreadPanelToggle(false);
    expect(ctx.threadPanelOpen.value).toBe(false);
    expect(ctx.activeThread.value).toBeNull();

    ctx.onThreadPanelToggle(true);
    expect(ctx.threadPanelOpen.value).toBe(false);
  });
});
