import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { effectScope, nextTick, ref, type EffectScope } from "vue";

const api = vi.hoisted(() => ({
  listConfigs: vi.fn(),
  getSessionConfig: vi.fn(),
  setSessionConfig: vi.fn(),
}));
vi.mock("@/api/v1", () => ({ chatApi: api }));
vi.mock("@/utils/toast", () => ({ useToast: () => ({ error: vi.fn() }) }));
import { createChatConfigSelection } from "./useChatConfigSelection";
import { resetAccountSession } from "@/utils/accountSession";
import {
  getStoredSelectedChatConfigId,
  setStoredSelectedChatConfigId,
} from "@/utils/chatConfigBinding";

const scopes: EffectScope[] = [];
function selection(sessionId: string | null) {
  const scope = effectScope();
  scopes.push(scope);
  const context = ref({ sessionId, platformId: "webchat", isGroup: false });
  const config = scope.run(() => {
    const config = createChatConfigSelection();
    config.bindContext(context);
    return config;
  })!;
  return { config, context };
}

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem("account_id", "alice");
  vi.resetAllMocks();
  api.listConfigs.mockResolvedValue({
    data: {
      data: {
        info_list: [
          { id: "default", name: "Default", agent_runner_type: "local" },
          { id: "research", name: "Research", agent_runner_type: "local" },
        ],
      },
    },
  });
  api.getSessionConfig.mockResolvedValue({
    data: { status: "ok", data: { config_id: "default" } },
  });
  api.setSessionConfig.mockResolvedValue({
    data: { status: "ok", data: { config_id: "research" } },
  });
});
afterEach(() => {
  scopes.splice(0).forEach((scope) => scope.stop());
  vi.restoreAllMocks();
});

describe("owned chat configuration", () => {
  it("waits for a pending selection before first send and stores a new-chat default immediately", async () => {
    const { config } = selection(null);
    await config.prepareSend();
    let finish!: () => void;
    // No session yet: selection is remembered before creation starts.
    const choose = config.selectConfig("research");
    expect(getStoredSelectedChatConfigId()).toBe("research");
    await choose;
    expect(await config.prepareSend()).toBe("research");
    const session = selection("a");
    await session.config.prepareSend();
    api.setSessionConfig.mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = () => resolve({ data: { status: "ok" } });
        }),
    );
    const change = session.config.selectConfig("research");
    await nextTick();
    let sent = false;
    const send = session.config.prepareSend().then((id) => {
      sent = true;
      return id;
    });
    await nextTick();
    expect(sent).toBe(false);
    finish();
    await change;
    expect(await send).toBe("research");
    expect(api.setSessionConfig).toHaveBeenCalledWith("a", "research");
  });

  it("ignores an old session response after switching sessions", async () => {
    let finish!: (value: unknown) => void;
    api.getSessionConfig.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const { config, context } = selection("old");
    await nextTick();
    await vi.waitFor(() =>
      expect(api.getSessionConfig).toHaveBeenCalledWith("old"),
    );
    context.value = { ...context.value, sessionId: "new" };
    await nextTick();
    await config.prepareSend();
    finish({ data: { status: "ok", data: { config_id: "research" } } });
    await nextTick();
    expect(config.selectedConfigId.value).toBe("default");
  });

  it.each(["request", "response"])(
    "blocks a stale configuration after the next session binding fails (%s) and allows an explicit retry",
    async (failure) => {
      vi.spyOn(console, "error").mockImplementation(() => {});
      api.getSessionConfig.mockResolvedValueOnce({
        data: { status: "ok", data: { config_id: "research" } },
      });
      const { config, context } = selection("a");
      expect(await config.prepareSend()).toBe("research");
      if (failure === "request") {
        api.getSessionConfig.mockRejectedValueOnce(
          new Error("Network unavailable"),
        );
      } else {
        api.getSessionConfig.mockResolvedValueOnce({
          data: { status: "error", message: "Resource is unavailable" },
        });
      }
      context.value = { ...context.value, sessionId: "b" };
      const send = vi.fn();
      const attempt = config
        .prepareSend()
        .then((configId) => send({ session_id: "b", config_id: configId }));
      await expect(attempt).rejects.toThrow();
      expect(send).not.toHaveBeenCalled();
      expect(api.setSessionConfig).not.toHaveBeenCalled();
      expect(config.selectedConfigId.value).toBe("");
      expect(getStoredSelectedChatConfigId()).toBe("research");

      await config.selectConfig("default");
      expect(api.setSessionConfig).toHaveBeenCalledTimes(1);
      expect(api.setSessionConfig).toHaveBeenCalledWith("b", "default");
      expect(await config.prepareSend()).toBe("default");
    },
  );

  it("cancels a send that started before its target session changed", async () => {
    let finish!: (value: unknown) => void;
    api.getSessionConfig.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const { config, context } = selection("a");
    await vi.waitFor(() => expect(finish).toBeTypeOf("function"));
    const send = vi.fn();
    const attempt = config.prepareSend().then((configId) => send(configId));
    const rejection = expect(attempt).rejects.toThrow("Chat session changed");
    context.value = { ...context.value, sessionId: "b" };
    await nextTick();
    expect(await config.prepareSend()).toBe("default");
    finish({ data: { status: "ok", data: { config_id: "research" } } });
    await rejection;
    expect(send).not.toHaveBeenCalled();
    expect(api.setSessionConfig).not.toHaveBeenCalled();
  });

  it("does not let a send awaiting configuration continue under the next account", async () => {
    let finish!: (value: unknown) => void;
    api.getSessionConfig.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const { config } = selection("a");
    await vi.waitFor(() => expect(finish).toBeTypeOf("function"));
    const send = config.prepareSend();
    const rejected = expect(send).rejects.toThrow("Account changed");
    resetAccountSession();
    localStorage.setItem("account_id", "bob");
    finish({ data: { status: "ok", data: { config_id: "research" } } });
    await rejected;
  });

  it("separates account defaults and rejects pending work from a signed-out account", async () => {
    setStoredSelectedChatConfigId("research");
    const { config } = selection("a");
    await config.prepareSend();
    let finish!: (value: unknown) => void;
    api.setSessionConfig.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const choose = config.selectConfig("research");
    await nextTick();
    await vi.waitFor(() => expect(finish).toBeTypeOf("function"));
    resetAccountSession();
    localStorage.setItem("account_id", "bob");
    expect(getStoredSelectedChatConfigId()).toBe("default");
    finish({ data: { status: "ok" } });
    await choose;
    expect(getStoredSelectedChatConfigId()).toBe("default");
    localStorage.setItem("account_id", "alice");
    expect(getStoredSelectedChatConfigId()).toBe("research");
  });
});
