import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { chatApi } from "@/api/v1";
import SubagentConfigurationDialog from "./SubagentConfigurationDialog.vue";

vi.mock("@/api/v1", () => ({ chatApi: { getSubagentConfiguration: vi.fn() } }));
vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({ tm: (key: string) => key }),
}));

const configuration = {
  provider_id: "provider-a",
  system_prompt: "Inspect the workspace and report findings.",
  tools: ["read_file", "shell"],
  skills: ["grilling"],
  blocked_tools: ["create_subagent"],
  execution_timeout: 600,
  context_inherit_mode: "normal",
  workdir: "/workspace",
};
const wrappers: Array<ReturnType<typeof mount>> = [];
const slotStub = { template: "<div><slot /></div>" };

function dialog() {
  const wrapper = mount(SubagentConfigurationDialog, {
    props: {
      sessionId: "session-1",
      run: { subagent_run_id: "run-1", agent_name: "worker" },
    },
    global: {
      stubs: {
        "v-dialog": slotStub,
        "v-card": slotStub,
        "v-card-title": slotStub,
        "v-card-text": slotStub,
        "v-card-actions": slotStub,
        "v-alert": slotStub,
        "v-btn": { template: "<button><slot /></button>" },
        "v-icon": true,
        "v-spacer": true,
        "v-progress-circular": true,
      },
    },
  });
  wrappers.push(wrapper);
  return wrapper;
}
beforeEach(() => {
  vi.mocked(chatApi.getSubagentConfiguration).mockResolvedValue({
    data: { status: "ok", data: configuration },
  } as never);
});
afterEach(() => {
  for (const wrapper of wrappers.splice(0)) wrapper.unmount();
  vi.resetAllMocks();
});

describe("Subagent configuration snapshots", () => {
  it("loads the requested invocation and displays prompt, provider, tools and skills", async () => {
    const wrapper = dialog();
    await flushPromises();
    expect(chatApi.getSubagentConfiguration).toHaveBeenCalledWith(
      "session-1",
      "run-1",
      { signal: expect.any(AbortSignal) },
    );
    for (const text of [
      configuration.provider_id,
      configuration.system_prompt,
      "read_file",
      "shell",
      "grilling",
      "/workspace",
      "600s",
    ])
      expect(wrapper.text()).toContain(text);
  });

  it("identifies legacy invocations without guessing from a same-name agent", async () => {
    vi.mocked(chatApi.getSubagentConfiguration).mockResolvedValue({
      data: { status: "ok", data: {} },
    } as never);
    const wrapper = dialog();
    await flushPromises();
    expect(wrapper.text()).toContain("subagentConfig.legacy");
    expect(wrapper.find(".config-prompt").exists()).toBe(false);
  });

  it("aborts an old request and ignores its late result after the invocation changes", async () => {
    let finish!: (value: any) => void;
    vi.mocked(chatApi.getSubagentConfiguration).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const wrapper = dialog();
    const firstSignal = vi.mocked(chatApi.getSubagentConfiguration).mock
      .calls[0][2]?.signal;
    await wrapper.setProps({
      run: { subagent_run_id: "run-2", agent_name: "replacement" },
    });
    await flushPromises();
    expect(firstSignal?.aborted).toBe(true);
    finish({ data: { status: "ok", data: { provider_id: "stale-provider" } } });
    await flushPromises();
    expect(wrapper.text()).toContain("provider-a");
    expect(wrapper.text()).not.toContain("stale-provider");
    wrapper.unmount();
    expect(
      vi.mocked(chatApi.getSubagentConfiguration).mock.calls[1][2]?.signal
        ?.aborted,
    ).toBe(true);
  });

  it("shows load failures and closes through its dialog action", async () => {
    vi.mocked(chatApi.getSubagentConfiguration).mockRejectedValue(
      new Error("unavailable"),
    );
    const wrapper = dialog();
    await flushPromises();
    expect(wrapper.text()).toContain("subagentConfig.loadFailed");
    await wrapper.find("button").trigger("click");
    expect(wrapper.emitted("close")).toHaveLength(1);
  });
});
