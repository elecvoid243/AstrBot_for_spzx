import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { parse } from "vue/compiler-sfc";
import ts from "typescript";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { nextTick, ref } from "vue";
import { usePendingFollowUps } from "@/composables/usePendingFollowUps";
import {
  accountSessionRevision,
  assertCurrentAccount,
  resetAccountSession,
} from "@/utils/accountSession";

const script = parse(
  readFileSync(resolve("src/components/chat/Chat.vue"), "utf8"),
).descriptor.scriptSetup!.content;
const ast = ts.createSourceFile(
  "Chat.ts",
  script,
  ts.ScriptTarget.Latest,
  true,
);
const code = ast.statements
  .filter(
    (node) =>
      ts.isFunctionDeclaration(node) &&
      [
        "sendQueuedFollowUp",
        "openLiveMode",
        "flushPendingFollowUps",
        "flushOldestPendingFollowUp",
      ].includes(node.name!.text),
  )
  .map(
    (node) =>
      ts.transpileModule(node.getText(ast), {
        compilerOptions: { target: ts.ScriptTarget.ES2022 },
      }).outputText,
  )
  .join("\n");

function setup() {
  let finish!: (value: string) => void;
  let fail!: (reason: Error) => void;
  const prepareConfig = vi.fn(
    () =>
      new Promise<string>((resolve, reject) => {
        finish = resolve;
        fail = reject;
      }),
  );
  const state = {
    pendingFollowUps: usePendingFollowUps(),
    flushingFollowUps: new Set<string>(),
    chatAccountRevision: accountSessionRevision.value,
    accountSessionRevision,
    assertCurrentAccount,
    nextTick,
    currSessionId: ref("current"),
    inputRef: ref({ prepareConfig }),
    isOrdinaryUser: ref(true),
    transportMode: ref("websocket"),
    enableStreaming: ref(true),
    liveSelection: ref(null),
    liveModeOpen: ref(false),
    newSession: vi.fn(),
    getSelectedProviderSelection: vi.fn(() => ({
      providerId: "current-provider",
      modelName: "current-model",
    })),
    getCurrentThinkingEffort: vi.fn(() => "high"),
    buildOutgoingParts: vi.fn(),
    createLocalExchange: vi.fn(() => ({ userRecord: {}, botRecord: {} })),
    updateTitleFromText: vi.fn(),
    sendMessageStream: vi.fn(),
  };
  const actions = new Function(
    ...Object.keys(state),
    `${code}; return { sendQueuedFollowUp, openLiveMode, flushPendingFollowUps, flushOldestPendingFollowUp };`,
  )(...Object.values(state));
  return {
    ...state,
    ...actions,
    prepareConfig,
    finish: (value: string) => finish(value),
    fail: (reason: Error) => fail(reason),
  };
}
beforeEach(() => {
  resetAccountSession();
  vi.spyOn(console, "error").mockImplementation(() => {});
});

describe("queued follow-ups and live mode use the target's prepared configuration", () => {
  it.each(["flushPendingFollowUps", "flushOldestPendingFollowUp"])(
    "retains unsent queued text when %s cannot prepare configuration",
    async (action) => {
      const s = setup();
      s.pendingFollowUps.enqueue("current", "private queued text");
      const pending = s[action]("current");
      s.fail(new Error("Configuration unavailable"));
      await pending;
      expect(
        s.pendingFollowUps.itemsFor("current").map((item: { text: string }) => item.text),
      ).toEqual(["private queued text"]);
      expect(s.sendMessageStream).not.toHaveBeenCalled();
    },
  );
  it("waits before dispatching a current-session follow-up", async () => {
    const s = setup();
    const pending = s.sendQueuedFollowUp("current", "queued text");
    expect(s.sendMessageStream).not.toHaveBeenCalled();
    s.finish("research");
    await pending;
    expect(s.sendMessageStream).toHaveBeenCalledWith(
      expect.objectContaining({
        sessionId: "current",
        configId: "research",
        selectedProvider: "current-provider",
        selectedModel: "current-model",
        thinkingEffort: "high",
        transport: "websocket",
      }),
    );
  });
  it("leaves a background target's persisted binding and model selection in place", async () => {
    const s = setup();
    await s.sendQueuedFollowUp("background", "queued text");
    expect(s.prepareConfig).not.toHaveBeenCalled();
    expect(s.sendMessageStream).toHaveBeenCalledWith(
      expect.objectContaining({
        sessionId: "background",
        configId: undefined,
        selectedProvider: "",
        selectedModel: "",
        thinkingEffort: "auto",
      }),
    );
  });
  it.each(["failure", "target", "account"])(
    "blocks a pending follow-up after %s changes",
    async (kind) => {
      const s = setup();
      const pending = s.sendQueuedFollowUp("current", "queued text");
      const rejected = expect(pending).rejects.toThrow();
      if (kind === "target") s.currSessionId.value = "another";
      if (kind === "account") resetAccountSession();
      if (kind === "failure") s.fail(new Error("Configuration unavailable"));
      else s.finish("research");
      await rejected;
      expect(s.createLocalExchange).not.toHaveBeenCalled();
      expect(s.sendMessageStream).not.toHaveBeenCalled();
    },
  );
  it("opens live mode only after preparing the current target", async () => {
    const s = setup();
    const pending = s.openLiveMode();
    await nextTick();
    expect(s.liveModeOpen.value).toBe(false);
    s.finish("research");
    await pending;
    expect(s.liveModeOpen.value).toBe(true);
    expect(s.liveSelection.value).toEqual({
      sessionId: "current",
      configId: "research",
      selectedProvider: "current-provider",
      selectedModel: "current-model",
      thinkingEffort: "high",
    });
  });
  it.each(["failure", "target", "account"])(
    "keeps live mode closed after %s changes during preparation",
    async (kind) => {
      const s = setup();
      const pending = s.openLiveMode();
      await nextTick();
      if (kind === "target") s.currSessionId.value = "another";
      if (kind === "account") resetAccountSession();
      if (kind === "failure") s.fail(new Error("Configuration unavailable"));
      else s.finish("research");
      await pending;
      expect(s.liveModeOpen.value).toBe(false);
    },
  );
});
