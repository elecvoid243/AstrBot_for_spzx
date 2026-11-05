import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { parse } from "vue/compiler-sfc";
import ts from "typescript";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { effectScope, ref, type EffectScope } from "vue";

const api = vi.hoisted(() => ({
  getSessionSelection: vi.fn(),
  setSessionSelection: vi.fn(),
}));
const error = vi.hoisted(() => vi.fn());
vi.mock("@/api/v1", () => ({ chatApi: api }));
vi.mock("@/utils/toast", () => ({ useToast: () => ({ error }) }));
const scopes: EffectScope[] = [];
const empty = {
  selected_provider: null,
  selected_model: null,
  thinking_effort: null,
  effort_preset: null,
};
const saved = new Map<string, typeof empty | any>();
const ok = (data: any) => ({ data: { status: "ok", data } });

async function setup(id = "a") {
  const { useProviderModelSelection } = await import(
    "./useProviderModelSelection"
  );
  const scope = effectScope();
  scopes.push(scope);
  const sessionId = ref(id);
  const store = scope.run(() => useProviderModelSelection(sessionId))!;
  await store.prepareSend();
  return { store, sessionId, useProviderModelSelection };
}

beforeEach(() => {
  vi.resetModules();
  vi.clearAllMocks();
  localStorage.clear();
  saved.clear();
  api.getSessionSelection.mockImplementation(async (id) =>
    ok(saved.get(id) || empty),
  );
  api.setSessionSelection.mockImplementation(async (id, selection) => {
    saved.set(id, selection);
    return ok(selection);
  });
});
afterEach(() => scopes.splice(0).forEach((scope) => scope.stop()));

describe("server-owned ordinary session choices", () => {
  it("loads server state despite stale browser choices and survives a new browser", async () => {
    saved.set("a", {
      ...empty,
      selected_provider: "provider-a",
      selected_model: "model-a",
      thinking_effort: "32768",
    });
    localStorage.setItem("selectedProvider", "stale");
    localStorage.setItem(
      "chat.sessionProvider.a",
      JSON.stringify({ providerId: "wrong" }),
    );
    const { store, useProviderModelSelection } = await setup();
    const header = useProviderModelSelection();
    expect(header.selectedProviderId.value).toBe("provider-a");
    expect(store.thinkingEffort.value).toBe("32768");
    await header.setSelection("provider-b", "model-b");
    await store.setThinkingEffort("xhigh", "custom-preset");
    await store.prepareSend();
    scopes.splice(0).forEach((scope) => scope.stop());
    localStorage.clear();
    const browser = await setup();
    expect(browser.store.selectedModelName.value).toBe("model-b");
    expect(browser.store.thinkingEffort.value).toBe("xhigh");
    expect(browser.store.effortPreset.value).toBe("custom-preset");
  });

  it("keeps a model and effort chosen in the empty composer when the first send creates a session", async () => {
    const { store, sessionId } = await setup("");
    await store.setSelection("chosen", "chosen-model");
    await store.setThinkingEffort("xhigh", "custom");
    const script = parse(
      readFileSync(resolve("src/components/chat/Chat.vue"), "utf8"),
    ).descriptor.scriptSetup!.content;
    const ast = ts.createSourceFile(
      "Chat.ts",
      script,
      ts.ScriptTarget.Latest,
      true,
    );
    let branch: ts.IfStatement | undefined;
    function visit(node: ts.Node) {
      if (
        ts.isIfStatement(node) &&
        node.expression.getText(ast) === "!sessionId" &&
        node.thenStatement.getText(ast).includes("const pendingModel")
      )
        branch = node;
      ts.forEachChild(node, visit);
    }
    visit(ast);
    expect(branch).toBeDefined();
    const code = ts.transpileModule(branch!.getText(ast), {
      compilerOptions: { target: ts.ScriptTarget.ES2022 },
    }).outputText;
    const dependencies = {
      getSelectedProviderSelection: () => ({
        providerId: store.selectedProviderId.value,
        modelName: store.selectedModelName.value,
      }),
      sessionThinkingEffort: store.thinkingEffort,
      sessionEffortPreset: store.effortPreset,
      newSession: async () => {
        sessionId.value = "created";
        return "created";
      },
      prepareModelSelection: store.prepareSend,
      setSessionModelSelection: store.setSelection,
      setSessionThinkingEffort: store.setThinkingEffort,
      getSessions: async () => {},
      chatDrafts: { clearDraft: vi.fn() },
      NEW_CHAT_DRAFT_KEY: "draft",
    };
    const send = new Function(
      ...Object.keys(dependencies),
      `return (async () => { let sessionId = ""; ${code}; return sessionId; })();`,
    );
    expect(await send(...Object.values(dependencies))).toBe("created");
    expect(saved.get("created")).toEqual({
      selected_provider: "chosen",
      selected_model: "chosen-model",
      thinking_effort: "xhigh",
      effort_preset: "custom",
    });
  });

  it("keeps unset overrides separate from the effective account defaults", async () => {
    saved.set("a", {
      ...empty,
      default_thinking_effort: "24576",
      default_effort_preset: "numeric",
    });
    const { store } = await setup();
    expect(store.thinkingEffort.value).toBeNull();
    expect(store.defaultThinkingEffort.value).toBe("24576");
    await store.setSelection("chosen", "model");
    expect(saved.get("a").thinking_effort).toBeNull();
  });

  it("isolates two sessions and leaves old sessions on server defaults", async () => {
    const { store, sessionId } = await setup();
    await store.setSelection("a", "model-a");
    await store.setThinkingEffort("0.75", "numeric");
    sessionId.value = "b";
    await store.prepareSend();
    expect(store.selectedProviderId.value).toBe("");
    expect(store.thinkingEffort.value).toBeNull();
    await store.setSelection("b", "model-b");
    sessionId.value = "a";
    await store.prepareSend();
    expect(store.selectedModelName.value).toBe("model-a");
    expect(store.thinkingEffort.value).toBe("0.75");
    expect(saved.get("b").thinking_effort).toBeNull();
  });

  it("attributes a pending save to its captured session and ignores stale loads", async () => {
    const { store, sessionId } = await setup();
    let finish!: (result: any) => void;
    api.setSessionSelection.mockImplementationOnce(
      (id, selection) =>
        new Promise((resolve) => {
          finish = () => {
            saved.set(id, selection);
            resolve(ok(selection));
          };
        }),
    );
    const saving = store.setSelection("a", "model-a");
    await Promise.resolve();
    await Promise.resolve();
    sessionId.value = "b";
    finish(null);
    await saving;
    await store.prepareSend();
    expect(saved.get("a").selected_model).toBe("model-a");
    expect(saved.has("b")).toBe(false);
    expect(store.selectedModelName.value).toBe("");
    let loadA!: (result: any) => void;
    api.getSessionSelection.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          loadA = resolve;
        }),
    );
    sessionId.value = "a";
    await vi.waitFor(() => expect(loadA).toBeTypeOf("function"));
    sessionId.value = "b";
    await store.prepareSend();
    loadA(ok(saved.get("a")));
    await Promise.resolve();
    expect(store.selectedModelName.value).toBe("");
  });

  it("reads the completed write when switching away and back during a save", async () => {
    const { store, sessionId } = await setup();
    let finish!: () => void;
    api.setSessionSelection.mockImplementationOnce(
      (id, selection) =>
        new Promise((resolve) => {
          finish = () => {
            saved.set(id, selection);
            resolve(ok(selection));
          };
        }),
    );
    const saving = store.setSelection("chosen", "latest-model");
    await Promise.resolve();
    await Promise.resolve();
    sessionId.value = "b";
    sessionId.value = "a";
    finish();
    await saving;
    await store.prepareSend();
    expect(store.selectedModelName.value).toBe("latest-model");
  });

  it("waits for an in-flight save before allowing a send", async () => {
    const { store } = await setup();
    let finish!: (result: any) => void;
    api.setSessionSelection.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    void store.setSelection("a", "model-a");
    let sent = false;
    const send = store.prepareSend().then(() => {
      sent = true;
    });
    await Promise.resolve();
    await Promise.resolve();
    expect(sent).toBe(false);
    finish(ok(empty));
    await send;
    expect(sent).toBe(true);
  });

  it("explains failed saves, rolls back the choice and blocks sends until corrected", async () => {
    const { store } = await setup();
    api.setSessionSelection.mockResolvedValueOnce({
      data: { status: "error", message: "Chat model is unavailable" },
    });
    await store.setSelection("removed", "model");
    expect(store.selectedProviderId.value).toBe("");
    expect(error).toHaveBeenCalledWith(
      expect.stringContaining("Chat model is unavailable"),
    );
    await expect(store.prepareSend()).rejects.toThrow("unavailable");
    await store.setSelection("valid", "model");
    await expect(store.prepareSend()).resolves.toBeUndefined();
  });

  it("discards a late response after account change", async () => {
    const { store } = await setup();
    let finish!: (result: any) => void;
    api.setSessionSelection.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const saving = store.setSelection("a", "private-model");
    await Promise.resolve();
    await Promise.resolve();
    const { resetAccountSession } = await import("@/utils/accountSession");
    resetAccountSession();
    finish(ok(empty));
    await saving;
    expect(store.selectedModelName.value).toBe("");
  });
});
