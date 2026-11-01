// Author: elecvoid243 @ 2026-10-22
// The side-chat lifecycle lives inside Chat.vue's script setup, so run its
// actual declarations, watchers and unmount hook in a mounted lifecycle harness.
// This does not mount the complete Chat component or verify browser rendering.
// Focus: closing the panel only hides it — side chats persist and are deleted
// explicitly from the side-chat menu.

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { parse } from "vue/compiler-sfc";
import ts from "typescript";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  accountSessionRevision,
  assertCurrentAccount,
} from "@/utils/accountSession";
import { flushPromises, mount } from "@vue/test-utils";
import type {
  FileReferenceTarget,
  FileReferenceNavigation,
} from "@/composables/fileReference";
import {
  computed,
  defineComponent,
  h,
  nextTick,
  onBeforeUnmount,
  reactive,
  ref,
  watch,
  type Ref,
} from "vue";

const script = parse(
  readFileSync(resolve("src/components/chat/Chat.vue"), "utf8"),
).descriptor.scriptSetup!.content;
const ast = ts.createSourceFile(
  "Chat.ts",
  script,
  ts.ScriptTarget.Latest,
  true,
);
const wanted = new Set([
  "closeThreadPanel",
  "onThreadPanelToggle",
  "openSideChat",
  "openSideChatPanel",
  "loadSideChats",
  "openSideChatMenu",
  "openThreadPanel",
  "openRefsSidebar",
  "openReasoningPanel",
  "toggleGitDiffSidebar",
  "closeTodoMenu",
  "openFileReference",
  "autoOpenAgentBrowser",
]);
const variables = new Set([
  "threadPanelOpen",
  "activeThread",
  "sideChatThreadId",
  "creatingSideChat",
  "sideChatGeneration",
  "sideChats",
  "sideChatMenuOpen",
  "loadingSideChats",
  "goalSidebarOpen",
  "todoMenuOpen",
  "todoMenuPlacement",
  "fileReferenceNavigation",
  "fileReferenceRequestId",
  "lastAgentBrowserRun",
]);
const declarations = ast.statements.filter((node) => {
  if (ts.isFunctionDeclaration(node)) return wanted.has(node.name!.text);
  if (ts.isVariableStatement(node)) {
    return node.declarationList.declarations.every((item) =>
      variables.has(item.name.getText(ast)),
    );
  }
  if (!ts.isExpressionStatement(node) || !ts.isCallExpression(node.expression))
    return false;
  const call = node.expression;
  return (
    (call.expression.getText(ast) === "onBeforeUnmount" &&
      node.getText(ast).includes("closeThreadPanel()")) ||
    (call.expression.getText(ast) === "watch" &&
      ((["currSessionId", "() => chatHeader.workspaceFilesOpen"].includes(
        call.arguments[0].getText(ast),
      ) &&
        node.getText(ast).includes("closeThreadPanel()")) ||
        (["goalSidebarOpen", "todoMenuOpen"].includes(
          call.arguments[0].getText(ast),
        ) &&
          node.getText(ast).includes("refsSidebarOpen.value"))))
  );
});
const code = declarations
  .map(
    (node) =>
      ts.transpileModule(node.getText(ast), {
        compilerOptions: { target: ts.ScriptTarget.ES2022 },
      }).outputText,
  )
  .join("\n");

interface ThreadRef {
  thread_id: string;
}

/**
 * Mount the extracted lifecycle with controlled HTTP responses.
 *
 * Returns:
 *   Component state, lifecycle actions and queued create responses.
 */
function setup() {
  const requests: Array<{
    resolve: (response: {
      data: { status: string; data?: ThreadRef; message?: string };
    }) => void;
    reject: (error: Error) => void;
  }> = [];
  const chatApi = {
    createThread: vi.fn(
      () =>
        new Promise((resolve, reject) => {
          requests.push({ resolve, reject });
        }),
    ),
    deleteThread: vi.fn().mockResolvedValue({}),
    listSideThreads: vi.fn().mockResolvedValue({
      data: { data: { threads: [] } },
    }),
  };
  const state = {
    currSessionId: ref("session-a"),
    chatHeader: reactive({
      workspaceFilesOpen: false,
      goalSidebarOpen: false,
      SET_WORKSPACE_FILES_OPEN(open: boolean) {
        this.workspaceFilesOpen = open;
      },
      SET_GOAL_SIDEBAR_OPEN(open: boolean) {
        this.goalSidebarOpen = open;
      },
      CLEAR_CONTEXT: vi.fn(),
    }),
    reasoningPanelOpen: ref(false),
    activeReasoningTarget: ref(null),
    refsSidebarOpen: ref(false),
    selectedRefs: ref(null),
    gitDiffSidebarOpen: ref(false),
    confirmFileNavigation: vi.fn().mockResolvedValue(true),
    gitDiffSidebarRef: ref({
      prepareAgentBrowser: vi.fn().mockResolvedValue(true),
    }),
    pluginExtensionApi: {
      get: vi.fn().mockResolvedValue({
        data: { status: "ok", data: { available: false } },
      }),
    },
    threadSelection: { visible: false },
    toast: { error: vi.fn() },
    tm: (key: string) => key,
    isAxiosError: (error: unknown) =>
      Boolean((error as { isAxiosError?: boolean })?.isAxiosError),
  };
  // The real unmount callback also disposes unrelated viewport/media resources.
  const environment = {
    chatAccountRevision: accountSessionRevision.value,
    assertCurrentAccount,
    ...state,
    chatApi,
    ref,
    computed,
    watch,
    onBeforeUnmount,
    viewportSessionToken: 0,
    viewportInteractionToken: 0,
    bottomScrollFrame: 0,
    userScrollSettleTimer: 0,
    endJumpLanding() {},
    endJumpScrollLock() {},
    historyLayoutObserver: null,
    readingViewportAnchor: null,
    endMessagesScrollGesture() {},
    composerResizeObserver: null,
    cleanupMediaCache() {},
    onSidebarResizeEnd() {},
    onSidebarKeydown() {},
    markerResizeObserver: null,
  };
  const run = new Function(
    ...Object.keys(environment),
    `${code}
    return { threadPanelOpen, activeThread, sideChatThreadId, creatingSideChat,
      sideChats, sideChatMenuOpen, loadingSideChats,
      goalSidebarOpen, todoMenuOpen, fileReferenceNavigation,
      ${[...wanted].join(", ")} };`,
  );
  let api!: {
    threadPanelOpen: Ref<boolean>;
    activeThread: Ref<ThreadRef | null>;
    sideChatThreadId: Ref<string | null>;
    creatingSideChat: Ref<boolean>;
    sideChats: Ref<Array<{ thread_id: string; title?: string }>>;
    sideChatMenuOpen: Ref<boolean>;
    loadingSideChats: Ref<boolean>;
    goalSidebarOpen: Ref<boolean>;
    todoMenuOpen: Ref<boolean>;
    fileReferenceNavigation: Ref<FileReferenceNavigation | null>;
    openFileReference: (target: FileReferenceTarget) => Promise<void>;
    autoOpenAgentBrowser: (
      sessionId: string,
      tool: { name: string },
      runId: string,
    ) => Promise<void>;
    closeThreadPanel: () => void;
    onThreadPanelToggle: (open: boolean) => void;
    openSideChat: () => Promise<void>;
    openSideChatMenu: () => void;
    openThreadPanel: (thread: ThreadRef) => void;
    openRefsSidebar: (refs: unknown) => void;
    openReasoningPanel: (payload: unknown) => void;
    toggleGitDiffSidebar: () => void;
  };
  const wrapper = mount(
    defineComponent({
      setup() {
        api = run(...Object.values(environment));
        return () => h("div");
      },
    }),
  );
  cleanups.push(() => wrapper.unmount());
  return {
    chatApi,
    requests,
    wrapper,
    ...state,
    ...api,
  };
}

const cleanups: Array<() => void> = [];
afterEach(() => {
  cleanups.splice(0).forEach((cleanup) => cleanup());
  vi.restoreAllMocks();
});

describe("Chat side-chat lifecycle", () => {
  it("opens the Agent browser for the current live reply, including an idle runtime", async () => {
    const ctx = setup();
    ctx.refsSidebarOpen.value = true;
    await ctx.autoOpenAgentBrowser(
      "session-a",
      { name: "browser_navigate" },
      "run-1",
    );
    expect(ctx.pluginExtensionApi.get).toHaveBeenCalledWith(
      "browser-use/status",
      {
        params: { chat_id: "session-a" },
      },
    );
    expect(
      ctx.gitDiffSidebarRef.value.prepareAgentBrowser,
    ).toHaveBeenCalledOnce();
    expect(ctx.gitDiffSidebarOpen.value).toBe(true);
    expect(ctx.refsSidebarOpen.value).toBe(false);
  });

  it("keeps an already open workspace view and ignores background or unrelated tools", async () => {
    const ctx = setup();
    await ctx.autoOpenAgentBrowser(
      "session-b",
      { name: "browser_click" },
      "background",
    );
    await ctx.autoOpenAgentBrowser("session-a", { name: "shell" }, "shell");
    await ctx.autoOpenAgentBrowser(
      "session-a",
      { name: "browser_close_all" },
      "close",
    );
    ctx.gitDiffSidebarOpen.value = true;
    await ctx.autoOpenAgentBrowser(
      "session-a",
      { name: "browser_navigate" },
      "open",
    );
    expect(ctx.pluginExtensionApi.get).not.toHaveBeenCalled();
    expect(
      ctx.gitDiffSidebarRef.value.prepareAgentBrowser,
    ).not.toHaveBeenCalled();
  });

  it("honors a manual close for the rest of the reply and allows the next reply", async () => {
    const ctx = setup();
    await ctx.autoOpenAgentBrowser(
      "session-a",
      { name: "browser_navigate" },
      "run-1",
    );
    ctx.gitDiffSidebarOpen.value = false;
    await ctx.autoOpenAgentBrowser(
      "session-a",
      { name: "browser_click" },
      "run-1",
    );
    expect(ctx.gitDiffSidebarOpen.value).toBe(false);
    expect(ctx.pluginExtensionApi.get).toHaveBeenCalledOnce();
    await ctx.autoOpenAgentBrowser(
      "session-a",
      { name: "browser_navigate" },
      "run-2",
    );
    expect(ctx.gitDiffSidebarOpen.value).toBe(true);
  });

  it.each(["session switch", "manual open", "another panel"])(
    "drops an automatic open after a %s",
    async (change) => {
      const ctx = setup();
      let finish!: (value: unknown) => void;
      ctx.pluginExtensionApi.get.mockReturnValueOnce(
        new Promise((resolve) => {
          finish = resolve;
        }) as never,
      );
      const pending = ctx.autoOpenAgentBrowser(
        "session-a",
        { name: "browser_navigate" },
        "run-1",
      );
      if (change === "session switch") ctx.currSessionId.value = "session-b";
      else if (change === "manual open") ctx.gitDiffSidebarOpen.value = true;
      else await ctx.openRefsSidebar({ title: "manual refs" });
      finish({
        data: { status: "ok", data: { available: true, generation: "g" } },
      });
      await pending;
      expect(
        ctx.gitDiffSidebarRef.value.prepareAgentBrowser,
      ).not.toHaveBeenCalled();
      expect(ctx.gitDiffSidebarOpen.value).toBe(change === "manual open");
    },
  );

  it("keeps plugin failures and declined navigation from changing panels", async () => {
    const ctx = setup();
    ctx.pluginExtensionApi.get.mockRejectedValueOnce(new Error("busy"));
    await ctx.autoOpenAgentBrowser(
      "session-a",
      { name: "browser_click" },
      "busy",
    );
    ctx.pluginExtensionApi.get.mockResolvedValueOnce({
      data: { status: "error", data: {} },
    } as never);
    await ctx.autoOpenAgentBrowser(
      "session-a",
      { name: "browser_click" },
      "missing",
    );
    ctx.gitDiffSidebarRef.value.prepareAgentBrowser.mockResolvedValueOnce(
      false,
    );
    await ctx.autoOpenAgentBrowser(
      "session-a",
      { name: "browser_click" },
      "denied",
    );
    expect(ctx.gitDiffSidebarOpen.value).toBe(false);
    expect(ctx.toast.error).not.toHaveBeenCalled();
  });

  it("waits for the editor before replacing a side chat with the shared file pane", async () => {
    const ctx = setup();
    await ctx.openThreadPanel({ thread_id: "side-a" });
    ctx.sideChatThreadId.value = "side-a";
    let allow!: (value: boolean) => void;
    ctx.confirmFileNavigation.mockReturnValueOnce(
      new Promise<boolean>((resolve) => {
        allow = resolve;
      }),
    );
    const target = { path: "C:/outside/file.py", startLine: 12, endLine: 18 };
    const pending = ctx.openFileReference(target);
    expect(ctx.threadPanelOpen.value).toBe(true);
    expect(ctx.fileReferenceNavigation.value).toBeNull();
    expect(ctx.gitDiffSidebarOpen.value).toBe(false);
    allow(true);
    await pending;
    expect(ctx.threadPanelOpen.value).toBe(false);
    expect(ctx.gitDiffSidebarOpen.value).toBe(true);
    expect(ctx.fileReferenceNavigation.value).toEqual({
      ...target,
      requestId: 1,
    });
    expect(ctx.chatApi.deleteThread).not.toHaveBeenCalled();
    await ctx.openThreadPanel({ thread_id: "side-a" });
    expect(ctx.activeThread.value?.thread_id).toBe("side-a");
  });

  it("does not publish or replay a cancelled citation", async () => {
    const ctx = setup();
    await ctx.openThreadPanel({ thread_id: "side-a" });
    ctx.confirmFileNavigation.mockResolvedValueOnce(false);
    await ctx.openFileReference({
      path: "G:/cancelled.py",
      startLine: null,
      endLine: null,
    });
    expect(ctx.fileReferenceNavigation.value).toBeNull();
    expect(ctx.threadPanelOpen.value).toBe(true);
    expect(ctx.activeThread.value?.thread_id).toBe("side-a");
    expect(ctx.gitDiffSidebarOpen.value).toBe(false);
    expect(ctx.chatApi.deleteThread).not.toHaveBeenCalled();
  });

  it.each(["new citation", "session", "panel"])(
    "discards a citation whose guard completes after a newer %s",
    async (change) => {
      const ctx = setup();
      let allow!: (value: boolean) => void;
      ctx.confirmFileNavigation.mockReturnValueOnce(
        new Promise<boolean>((resolve) => {
          allow = resolve;
        }),
      );
      const pending = ctx.openFileReference({
        path: "G:/old.py",
        startLine: null,
        endLine: null,
      });
      if (change === "new citation")
        await ctx.openFileReference({
          path: "C:/new.py",
          startLine: 3,
          endLine: 3,
        });
      if (change === "session") {
        ctx.currSessionId.value = "session-b";
        await nextTick();
      }
      if (change === "panel") ctx.closeThreadPanel();
      allow(true);
      await pending;
      expect(ctx.fileReferenceNavigation.value?.path ?? null).toBe(
        change === "new citation" ? "C:/new.py" : null,
      );
    },
  );

  it("opens a same-session side chat with isolated context", async () => {
    const ctx = setup();
    ctx.goalSidebarOpen.value = true;
    ctx.todoMenuOpen.value = true;
    await nextTick();
    const pending = ctx.openSideChat();
    expect(ctx.creatingSideChat.value).toBe(true);
    expect(ctx.chatApi.createThread).toHaveBeenCalledWith({
      session_id: "session-a",
      side_chat: true,
    });

    ctx.requests[0].resolve({
      data: { status: "ok", data: { thread_id: "side-a" } },
    });
    await pending;

    expect(ctx.activeThread.value?.thread_id).toBe("side-a");
    expect(ctx.sideChatThreadId.value).toBe("side-a");
    expect(ctx.threadPanelOpen.value).toBe(true);
    expect(ctx.creatingSideChat.value).toBe(false);
    expect(ctx.goalSidebarOpen.value).toBe(false);
    expect(ctx.todoMenuOpen.value).toBe(false);
    expect(ctx.chatApi.deleteThread).not.toHaveBeenCalled();
  });

  it("cleans up a side-chat thread created after the session switched", async () => {
    const ctx = setup();
    const pending = ctx.openSideChat();
    ctx.currSessionId.value = "session-b";
    await nextTick();

    ctx.requests[0].resolve({
      data: { status: "ok", data: { thread_id: "late-a" } },
    });
    await pending;

    expect(ctx.threadPanelOpen.value).toBe(false);
    expect(ctx.activeThread.value).toBeNull();
    expect(ctx.chatApi.deleteThread).toHaveBeenCalledTimes(1);
    expect(ctx.chatApi.deleteThread).toHaveBeenCalledWith("late-a");
  });

  it("preserves a branch selected while side-chat creation is pending", async () => {
    const ctx = setup();
    const pending = ctx.openSideChat();
    await ctx.openThreadPanel({ thread_id: "branch-new" });

    ctx.requests[0].resolve({
      data: { status: "ok", data: { thread_id: "late-side" } },
    });
    await pending;

    expect(ctx.activeThread.value?.thread_id).toBe("branch-new");
    expect(ctx.threadPanelOpen.value).toBe(true);
    expect(ctx.sideChatThreadId.value).toBeNull();
    expect(ctx.chatApi.deleteThread).toHaveBeenCalledTimes(1);
    expect(ctx.chatApi.deleteThread).toHaveBeenCalledWith("late-side");
    ctx.closeThreadPanel();
    expect(ctx.chatApi.deleteThread).not.toHaveBeenCalledWith("branch-new");
  });

  it.each([false, true])(
    "invalidates A -> B -> A even with batched updates: %s",
    async (batched) => {
      const ctx = setup();
      const pending = ctx.openSideChat();
      ctx.currSessionId.value = "session-b";
      if (!batched) await nextTick();
      ctx.currSessionId.value = "session-a";
      await nextTick();

      ctx.requests[0].resolve({
        data: { status: "ok", data: { thread_id: "obsolete-a" } },
      });
      await pending;

      expect(ctx.threadPanelOpen.value).toBe(false);
      expect(ctx.activeThread.value).toBeNull();
      expect(ctx.chatApi.deleteThread).toHaveBeenCalledWith("obsolete-a");
    },
  );

  it("cleans up a thread created after the real unmount hook runs", async () => {
    const ctx = setup();
    const pending = ctx.openSideChat();
    ctx.wrapper.unmount();

    ctx.requests[0].resolve({
      data: { status: "ok", data: { thread_id: "after-unmount" } },
    });
    await pending;

    expect(ctx.threadPanelOpen.value).toBe(false);
    expect(ctx.activeThread.value).toBeNull();
    expect(ctx.chatApi.deleteThread).toHaveBeenCalledWith("after-unmount");
    expect(ctx.toast.error).not.toHaveBeenCalled();
  });

  it.each(["refs", "reasoning", "git", "workspace", "goal", "todo"])(
    "preserves the newer %s selection",
    async (sidebar) => {
      const ctx = setup();
      const pending = ctx.openSideChat();
      if (sidebar === "refs") await ctx.openRefsSidebar({ title: "new refs" });
      if (sidebar === "reasoning")
        await ctx.openReasoningPanel({ blockIndex: 1 });
      if (sidebar === "git") await ctx.toggleGitDiffSidebar();
      if (sidebar === "workspace") ctx.chatHeader.workspaceFilesOpen = true;
      if (sidebar === "goal") ctx.goalSidebarOpen.value = true;
      if (sidebar === "todo") ctx.todoMenuOpen.value = true;
      await nextTick();

      ctx.requests[0].resolve({
        data: { status: "ok", data: { thread_id: "obsolete-side" } },
      });
      await pending;

      expect(ctx.threadPanelOpen.value).toBe(false);
      expect(ctx.activeThread.value).toBeNull();
      expect(ctx.chatApi.deleteThread).toHaveBeenCalledWith("obsolete-side");
      if (sidebar === "refs") {
        expect(ctx.refsSidebarOpen.value).toBe(true);
        expect(ctx.selectedRefs.value).toEqual({ title: "new refs" });
      }
      if (sidebar === "reasoning") {
        expect(ctx.reasoningPanelOpen.value).toBe(true);
        expect(ctx.activeReasoningTarget.value).toEqual({ blockIndex: 1 });
      }
      if (sidebar === "git") expect(ctx.gitDiffSidebarOpen.value).toBe(true);
      if (sidebar === "workspace")
        expect(ctx.chatHeader.workspaceFilesOpen).toBe(true);
      if (sidebar === "goal") expect(ctx.goalSidebarOpen.value).toBe(true);
      if (sidebar === "todo") expect(ctx.todoMenuOpen.value).toBe(true);
    },
  );

  it.each(["goal", "todo"])(
    "retains an existing branch when opening %s without a pending create",
    async (panel) => {
      const ctx = setup();
      await ctx.openThreadPanel({ thread_id: "retained-branch" });
      if (panel === "goal") ctx.goalSidebarOpen.value = true;
      else ctx.todoMenuOpen.value = true;
      await nextTick();

      expect(ctx.threadPanelOpen.value).toBe(true);
      expect(ctx.activeThread.value?.thread_id).toBe("retained-branch");
      expect(ctx.chatApi.deleteThread).not.toHaveBeenCalled();
    },
  );

  it.each(["success", "status-error", "network-error"])(
    "keeps a newer request busy when the old request returns %s",
    async (result) => {
      const ctx = setup();
      const oldPending = ctx.openSideChat();
      ctx.currSessionId.value = "session-b";
      await nextTick();
      const newPending = ctx.openSideChat();
      expect(ctx.requests).toHaveLength(2);
      expect(ctx.chatApi.createThread).toHaveBeenLastCalledWith({
        session_id: "session-b",
        side_chat: true,
      });

      if (result === "network-error")
        ctx.requests[0].reject(new Error("old network error"));
      else
        ctx.requests[0].resolve({
          data:
            result === "success"
              ? { status: "ok", data: { thread_id: "old-side" } }
              : { status: "error", message: "old status error" },
        });
      await oldPending;

      expect(ctx.creatingSideChat.value).toBe(true);
      expect(ctx.threadPanelOpen.value).toBe(false);
      expect(ctx.toast.error).not.toHaveBeenCalled();
      ctx.requests[1].resolve({
        data: { status: "ok", data: { thread_id: "new-side" } },
      });
      await newPending;
      expect(ctx.creatingSideChat.value).toBe(false);
      expect(ctx.activeThread.value?.thread_id).toBe("new-side");
      expect(ctx.chatApi.deleteThread).not.toHaveBeenCalledWith("new-side");
    },
  );

  it("preserves a newer side chat when it completes before the stale request", async () => {
    const ctx = setup();
    const oldPending = ctx.openSideChat();
    ctx.closeThreadPanel();
    const newPending = ctx.openSideChat();
    expect(ctx.requests).toHaveLength(2);
    ctx.requests[1].resolve({
      data: { status: "ok", data: { thread_id: "new-side" } },
    });
    await newPending;
    ctx.requests[0].resolve({
      data: { status: "ok", data: { thread_id: "old-side" } },
    });
    await oldPending;

    expect(ctx.threadPanelOpen.value).toBe(true);
    expect(ctx.activeThread.value?.thread_id).toBe("new-side");
    expect(ctx.sideChatThreadId.value).toBe("new-side");
    expect(ctx.chatApi.deleteThread).toHaveBeenCalledTimes(1);
    expect(ctx.chatApi.deleteThread).toHaveBeenCalledWith("old-side");
  });

  it.each(["status-error", "network-error", "missing-thread"])(
    "silences stale %s after teardown",
    async (result) => {
      const ctx = setup();
      const pending = ctx.openSideChat();
      ctx.wrapper.unmount();
      if (result === "network-error")
        ctx.requests[0].reject(new Error("network"));
      else
        ctx.requests[0].resolve({
          data:
            result === "missing-thread"
              ? { status: "ok" }
              : { status: "error", message: "create failed" },
        });
      await pending;

      expect(ctx.toast.error).not.toHaveBeenCalled();
      expect(ctx.threadPanelOpen.value).toBe(false);
      expect(ctx.chatApi.deleteThread).not.toHaveBeenCalled();
    },
  );

  it.each(["status-error", "network-error", "missing-thread"])(
    "still reports current-session %s and allows retry",
    async (result) => {
      vi.spyOn(console, "error").mockImplementation(() => {});
      const ctx = setup();
      const pending = ctx.openSideChat();
      if (result === "network-error")
        ctx.requests[0].reject(new Error("network"));
      else
        ctx.requests[0].resolve({
          data:
            result === "missing-thread"
              ? { status: "ok" }
              : { status: "error", message: "create failed" },
        });
      await pending;

      expect(ctx.toast.error).toHaveBeenCalledTimes(1);
      expect(ctx.toast.error).toHaveBeenCalledWith(
        result === "status-error" ? "create failed" : "thread.createFailed",
      );
      expect(ctx.threadPanelOpen.value).toBe(false);
      expect(ctx.creatingSideChat.value).toBe(false);
      const retry = ctx.openSideChat();
      expect(ctx.requests).toHaveLength(2);
      ctx.requests[1].resolve({
        data: { status: "ok", data: { thread_id: "retry-side" } },
      });
      await retry;
      expect(ctx.activeThread.value?.thread_id).toBe("retry-side");
    },
  );

  it("keeps the selected branch when cleaning up a stale side chat fails", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const ctx = setup();
    const pending = ctx.openSideChat();
    await ctx.openThreadPanel({ thread_id: "kept-branch" });
    ctx.chatApi.deleteThread.mockRejectedValue(new Error("delete failed"));
    ctx.requests[0].resolve({
      data: { status: "ok", data: { thread_id: "stale-side" } },
    });
    await pending;
    await flushPromises();

    expect(ctx.activeThread.value?.thread_id).toBe("kept-branch");
    expect(ctx.threadPanelOpen.value).toBe(true);
    expect(ctx.chatApi.deleteThread).toHaveBeenCalledTimes(1);
    expect(ctx.chatApi.deleteThread).toHaveBeenCalledWith("stale-side");
    expect(ctx.toast.error).not.toHaveBeenCalled();
    expect(errorSpy).toHaveBeenCalled();
  });

  it("closes a branch thread without deleting it", () => {
    const ctx = setup();
    ctx.activeThread.value = { thread_id: "branch-1" };

    ctx.closeThreadPanel();

    expect(ctx.threadPanelOpen.value).toBe(false);
    expect(ctx.activeThread.value).toBeNull();
    expect(ctx.chatApi.deleteThread).not.toHaveBeenCalled();
  });

  it("closes a side chat without deleting the backend thread", () => {
    const ctx = setup();
    ctx.activeThread.value = { thread_id: "side-1" };
    ctx.sideChatThreadId.value = "side-1";

    ctx.closeThreadPanel();

    expect(ctx.threadPanelOpen.value).toBe(false);
    expect(ctx.activeThread.value).toBeNull();
    expect(ctx.sideChatThreadId.value).toBeNull();
    expect(ctx.chatApi.deleteThread).not.toHaveBeenCalled();
  });

  it("routes ThreadPanel's close event through the same teardown", () => {
    const ctx = setup();
    ctx.activeThread.value = { thread_id: "side-3" };
    ctx.sideChatThreadId.value = "side-3";

    ctx.onThreadPanelToggle(false);
    expect(ctx.threadPanelOpen.value).toBe(false);
    expect(ctx.activeThread.value).toBeNull();
    expect(ctx.sideChatThreadId.value).toBeNull();
    expect(ctx.chatApi.deleteThread).not.toHaveBeenCalled();

    ctx.onThreadPanelToggle(true);
    expect(ctx.chatApi.deleteThread).not.toHaveBeenCalled();
    expect(ctx.threadPanelOpen.value).toBe(false);
  });

  it("lists the session's persistent side chats when the menu opens", async () => {
    const ctx = setup();
    ctx.chatApi.listSideThreads.mockResolvedValue({
      data: {
        data: { threads: [{ thread_id: "side-old", title: "旧问题" }] },
      },
    });

    ctx.openSideChatMenu();
    await flushPromises();

    expect(ctx.chatApi.listSideThreads).toHaveBeenCalledWith("session-a");
    expect(ctx.sideChats.value).toEqual([
      { thread_id: "side-old", title: "旧问题" },
    ]);
    expect(ctx.sideChatMenuOpen.value).toBe(true);
    expect(ctx.loadingSideChats.value).toBe(false);
  });
});
