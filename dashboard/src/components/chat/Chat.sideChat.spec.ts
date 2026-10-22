// Author: elecvoid243 @ 2026-10-22
// The side-chat lifecycle lives inside Chat.vue's script setup, so run its
// actual declarations with controlled refs (same technique as Chat.scroll).
// Focus: closing a temporary side chat must delete its backend thread, while
// closing a branch thread must not.

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
  const chatApi = { deleteThread: vi.fn().mockResolvedValue({}) };
  const activeThread: Ref<ThreadRef | null> = ref(null);
  const sideChatThreadId: Ref<string | null> = ref(null);
  const threadPanelOpen = ref(true);
  const api = new Function(
    "chatApi",
    "activeThread",
    "sideChatThreadId",
    "threadPanelOpen",
    `${code}
     return { closeThreadPanel, onThreadPanelToggle };`,
  )(chatApi, activeThread, sideChatThreadId, threadPanelOpen) as {
    closeThreadPanel: () => void;
    onThreadPanelToggle: (open: boolean) => void;
  };
  return {
    chatApi,
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
  it("closes a branch thread without deleting it", () => {
    const ctx = setup();
    ctx.activeThread.value = { thread_id: "branch-1" };

    ctx.closeThreadPanel();

    expect(ctx.threadPanelOpen.value).toBe(false);
    expect(ctx.activeThread.value).toBeNull();
    expect(ctx.chatApi.deleteThread).not.toHaveBeenCalled();
  });

  it("deletes the backend thread when closing a side chat", () => {
    const ctx = setup();
    ctx.activeThread.value = { thread_id: "side-1" };
    ctx.sideChatThreadId.value = "side-1";

    ctx.closeThreadPanel();

    expect(ctx.threadPanelOpen.value).toBe(false);
    expect(ctx.activeThread.value).toBeNull();
    expect(ctx.sideChatThreadId.value).toBeNull();
    expect(ctx.chatApi.deleteThread).toHaveBeenCalledWith("side-1");
  });

  it("survives a failed delete without raising", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const ctx = setup();
    ctx.activeThread.value = { thread_id: "side-2" };
    ctx.sideChatThreadId.value = "side-2";
    ctx.chatApi.deleteThread.mockRejectedValue(new Error("network"));

    expect(() => ctx.closeThreadPanel()).not.toThrow();
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(ctx.threadPanelOpen.value).toBe(false);
    expect(errorSpy).toHaveBeenCalled();
  });

  it("routes ThreadPanel's close event through the same teardown", () => {
    const ctx = setup();
    ctx.activeThread.value = { thread_id: "side-3" };
    ctx.sideChatThreadId.value = "side-3";

    ctx.onThreadPanelToggle(false);
    expect(ctx.chatApi.deleteThread).toHaveBeenCalledWith("side-3");

    ctx.chatApi.deleteThread.mockClear();
    ctx.onThreadPanelToggle(true);
    expect(ctx.chatApi.deleteThread).not.toHaveBeenCalled();
    expect(ctx.threadPanelOpen.value).toBe(false);
  });
});
