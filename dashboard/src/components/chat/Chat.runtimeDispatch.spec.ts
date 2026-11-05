import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { parse } from "vue/compiler-sfc";
import ts from "typescript";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { nextTick, ref } from "vue";
import { usePendingFollowUps } from "@/composables/usePendingFollowUps";
import { pluginExtensionApi } from "@/api/v1";
import { projectRelativePathStrict } from "@/composables/pathUtils";
import { useSpcodeProjectStatus } from "@/composables/useSpcodeProjectStatus";
import {
  useSpcodeProjectAutoLoad,
  isSessionLoadedTag,
  markSessionLoadedTag,
  ProjectLoadError,
} from "@/composables/useSpcodeProjectAutoLoad";
import {
  accountSessionRevision,
  assertCurrentAccount,
  resetAccountSession,
} from "@/utils/accountSession";

vi.mock("@/api/v1", () => ({
  pluginExtensionApi: { get: vi.fn(), post: vi.fn() },
}));

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
        "tryAutoLoadSpcodeForSession",
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
  it.each(["project", "custom"])(
    "repairs a changed %s workspace even when status is rejected and the old load tag exists",
    async (workspaceType) => {
      const origin = "webchat:FriendMessage:webchat!owner!session";
      const project = {
        project_id: "same-project",
        workspace_type: workspaceType,
        workspace_path: "C:/owned/current",
        resolved_workspace_path: "C:/owned/current",
      };
      const spcodeStatus = useSpcodeProjectStatus();
      const get = vi.mocked(pluginExtensionApi.get);
      const post = vi.mocked(pluginExtensionApi.post);
      get.mockReset();
      post.mockReset();
      get.mockResolvedValueOnce({
        data: {
          data: {
            loaded: true,
            directory: "C:/owned/old",
            umo: origin,
            boot_id: "same-boot",
          },
        },
      } as never);
      await spcodeStatus.refresh(origin);
      markSessionLoadedTag("session", project.project_id, "same-boot");
      get.mockRejectedValueOnce({ response: { status: 403 } });
      get.mockResolvedValueOnce({
        data: {
          data: {
            loaded: true,
            directory: "C:/owned/current",
            umo: origin,
            boot_id: "same-boot",
          },
        },
      } as never);
      post.mockResolvedValueOnce({
        data: {
          data: {
            success: false,
            reason: "no_project_loaded",
            previous_directory: "C:/owned/old",
          },
        },
      } as never);
      post.mockResolvedValueOnce({
        data: {
          data: { success: true, loaded: true, directory: "C:/owned/current" },
        },
      } as never);
      const dependencies = {
        resolveProjectForAutoLoad: () => project,
        chatAccountRevision: accountSessionRevision.value,
        accountSessionRevision,
        assertCurrentAccount,
        spcodeStatus,
        projectRelativePathStrict,
        isSessionLoadedTag,
        markSessionLoadedTag,
        silentLoad: useSpcodeProjectAutoLoad().silentLoad,
        operationProgress: { startPolling: vi.fn() },
        ProjectLoadError,
        toast: { error: vi.fn() },
        tm: (key: string) => key,
      };
      const autoLoad = new Function(
        ...Object.keys(dependencies),
        `${code}; return tryAutoLoadSpcodeForSession;`,
      )(...Object.values(dependencies));
      await autoLoad(origin, "session");
      expect(post.mock.calls.map((call) => [call[0], call[1]])).toMatchObject([
        [
          "spcode/project-load",
          { directory: "C:/owned/current", umo: origin, force: false },
        ],
        [
          "spcode/project-load",
          { directory: "C:/owned/current", umo: origin, force: true },
        ],
      ]);
      expect(spcodeStatus.status.value.directory).toBe("C:/owned/current");
      expect(dependencies.toast.error).not.toHaveBeenCalled();
    },
  );

  it.each(["flushPendingFollowUps", "flushOldestPendingFollowUp"])(
    "retains unsent queued text when %s cannot prepare configuration",
    async (action) => {
      const s = setup();
      s.pendingFollowUps.enqueue("current", "private queued text");
      const pending = s[action]("current");
      s.fail(new Error("Configuration unavailable"));
      await pending;
      expect(
        s.pendingFollowUps
          .itemsFor("current")
          .map((item: { text: string }) => item.text),
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
        thinkingEffort: undefined,
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
