// Author: impl_task10
// Date: 2026-11-16
//
// Shell contract for the developer-mode snapshot inspector: three distinct
// states, a grouped request rail, the detail hand-off, the collapsed
// diagnostics/raw sections and the header actions. The real composable runs
// against a mocked transport so the list -> detail flow is exercised without
// network access.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { defineComponent, h } from "vue";
import ChatDevModeInspector from "@/components/chat/ChatDevModeInspector.vue";
import type {
  LlmRequestSnapshotEnvelope,
  LlmSnapshotDetail,
  LlmSnapshotLimits,
} from "@/composables/llmRequestSnapshots";

const api = vi.hoisted(() => ({ list: vi.fn(), get: vi.fn() }));

vi.mock("@/api/v1", () => ({
  llmSnapshotsApi: api,
}));

// Self-contained zh-CN copy from spec section 10.4 plus the small technical
// labels the detail sections need. No locale files are loaded or modified.
const COPY = vi.hoisted(() => ({
  "devMode.title": "请求快照",
  "devMode.subtitle": "本回合 {count} 次请求 · 另有 {aux} 次辅助调用",
  "devMode.kind.chat": "主对话",
  "devMode.kind.compression": "压缩摘要",
  "devMode.kind.title": "标题生成",
  "devMode.kind.aux": "辅助调用",
  "devMode.section.overview": "概览",
  "devMode.section.response": "响应",
  "devMode.section.system": "系统提示词",
  "devMode.section.tools": "工具",
  "devMode.section.messages": "消息",
  "devMode.section.injections": "额外注入",
  "devMode.section.diagnostics": "诊断",
  "devMode.section.raw": "原始 JSON",
  "devMode.diff.title": "相对上一步",
  "devMode.state.disabled":
    "开发者模式未开启。开启后会记录每次 LLM 请求的完整上下文，仅保存在本机内存。",
  "devMode.state.empty": "本回合还没有请求快照。发送一条消息后再看。",
  "devMode.state.expired": "快照已过期。服务重启或超过 2 小时后会清空。",
  "devMode.state.select": "选择一条请求快照",
  "devMode.notice.memory":
    "快照仅保存在内存 · 2 小时后过期 · 关闭开发者模式即停止采集",
  "devMode.notice.redacted": "已脱敏 {count} 处",
  "devMode.action.copy": "复制 JSON",
  "devMode.action.download": "下载",
  "devMode.action.refresh": "刷新",
  "devMode.action.close": "关闭",
  "devMode.download.done": "已下载",
  "devMode.download.failed": "下载失败",
  "devMode.rail.label": "快照列表",
  "devMode.settings.title": "开发者模式",
  "devMode.settings.switch": "记录 LLM 请求快照",
  "devMode.settings.limits": "每会话最多 {count} 条 · {size} · {ttl} 后过期",
  "devMode.group.turn": "本回合",
  "devMode.group.aux": "本会话 · 辅助",
  "devMode.rail.compression": "压缩",
  "devMode.rail.error": "error",
  "devMode.rail.retry": "retry",
  "devMode.rail.cache": "缓存 {percent}%",
  "devMode.rail.tools": "+{count} tool",
  "devMode.truncated": "已截断，原始大小 {size}",
  "devMode.provenance.approx": "近似定位",
  "devMode.copy.failed": "复制失败",
  "devMode.tool.searchPlaceholder": "搜索工具名称、描述或来源",
  "devMode.tool.empty": "没有匹配的工具",
  "devMode.error": "加载失败：{message}",
  "copy.copy": "复制",
  "copy.copied": "已复制",
  "llmRequestInjections.title": "本轮上下文注入",
  "llmRequestInjections.count": "{count} 项",
  "llmRequestInjections.note": "插件在 LLM 请求时注入",
  "llmRequestInjections.units.chars": "{delta} 字符",
  "llmRequestInjections.units.items": "{delta} 条",
  "llmRequestInjections.units.tools": "{delta} 个工具",
  "llmRequestInjections.lossySuffix": "已截断",
  "llmRequestInjections.rewritten": "已改写",
}));

vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({
    tm: (key: string, params?: Record<string, string | number>) => {
      const template = (COPY as Record<string, string>)[key] ?? key;
      if (!params) return template;
      return template.replace(/\{(\w+)\}/g, (match, name: string) =>
        name in params ? String(params[name]) : match,
      );
    },
    getRaw: () => null,
  }),
}));

const LIMITS: LlmSnapshotLimits = {
  max_bytes_per_snapshot: 524288,
  max_snapshots_per_session: 128,
  max_bytes_per_session: 16777216,
  max_total_bytes: 67108864,
  max_message_bindings: 1024,
  ttl_seconds: 7200,
};

function envelope(
  snapshotId: string,
  extra: Record<string, unknown> = {},
): LlmRequestSnapshotEnvelope {
  return {
    snapshot_id: snapshotId,
    session_id: "s1",
    status: "ok",
    ...extra,
  } as LlmRequestSnapshotEnvelope;
}

const ITEMS: LlmRequestSnapshotEnvelope[] = [
  envelope("snap-1", {
    kind: "chat",
    step: 1,
    provider: { model: "model-a", host: "api.example.com" },
    duration_ms: 1200,
    counts: { tools: 2, messages: 4 },
    diagnostics: { cache: { hit_ratio: 0.1 } },
  }),
  envelope("snap-2", {
    kind: "chat",
    step: 2,
    provider: { model: "model-b", host: "api.example.com" },
    duration_ms: 8300,
    counts: { tools: 2, messages: 5 },
    tool_executions: [
      { call_id: "call_a1", name: "read_file", status: "ok", duration_ms: 312 },
    ],
    diagnostics: {
      compression: { strategy: "llm_summary" },
      retries: { count: 1 },
    },
  }),
  envelope("snap-aux", {
    kind: "compression",
    status: "ok",
    provider: { model: "model-c" },
    duration_ms: 300,
  }),
];

function detailFor(item: LlmRequestSnapshotEnvelope): LlmSnapshotDetail {
  return {
    envelope: item,
    raw: { model: "model", messages: [{ role: "user", content: "hello" }] },
    view: {
      system: {
        text: "You are AstrBot.",
        hash: "system-hash",
        source_field: "messages[0]",
        parts: [],
      },
      messages: [
        {
          index: 0,
          role: "system",
          content: [{ type: "text", text: "You are AstrBot." }],
          chars: 16,
          estimated_tokens: 8,
        },
        {
          index: 1,
          role: "user",
          content: [{ type: "text", text: "hello" }],
          chars: 5,
          estimated_tokens: 3,
        },
      ],
      tools: [
        {
          name: "read_file",
          description: "Read a text file from the workspace.",
          parameters: { type: "object", properties: { path: { type: "string" } } },
          origin: "builtin",
          active: true,
          denied: false,
          schema_bytes: 120,
        },
      ],
      params: { temperature: 0.7 },
      extra: {},
    },
  };
}

function listPayload(
  items: LlmRequestSnapshotEnvelope[],
  expired = false,
) {
  return {
    data: {
      data: {
        enabled: true,
        expired,
        items,
        limits: LIMITS,
        dropped: 0,
      },
    },
  };
}

const DETAILS = Object.fromEntries(
  ITEMS.map((item) => [item.snapshot_id, detailFor(item)]),
);

const VSwitchStub = defineComponent({
  name: "VSwitch",
  props: { modelValue: { type: Boolean, default: false } },
  emits: ["update:modelValue"],
  setup(props, { emit }) {
    return () =>
      h("button", {
        type: "button",
        class: "v-switch-stub",
        "aria-checked": String(props.modelValue),
        onClick: () => emit("update:modelValue", !props.modelValue),
      });
  },
});

async function mountOpen(
  props: Partial<{
    modelValue: boolean;
    sessionId: string;
    enabled: boolean;
    canEdit: boolean;
    initialTurnId: string;
  }> = {},
) {
  const wrapper = mount(ChatDevModeInspector, {
    props: {
      modelValue: true,
      sessionId: "s1",
      enabled: true,
      canEdit: false,
      ...props,
    },
    global: { components: { "v-switch": VSwitchStub } },
  });
  await flushPromises();
  await flushPromises();
  return wrapper;
}

describe("ChatDevModeInspector", () => {
  beforeEach(() => {
    api.list.mockReset();
    api.get.mockReset();
    api.list.mockResolvedValue(listPayload(ITEMS));
    api.get.mockImplementation(async (snapshotId: string) => ({
      data: { data: DETAILS[snapshotId] },
    }));
  });

  it("renders disabled, empty, and expired states distinctly", async () => {
    const disabled = await mountOpen({ enabled: false });
    expect(disabled.text()).toContain("开发者模式未开启");
    expect(api.list).not.toHaveBeenCalled();
    disabled.unmount();

    api.list.mockResolvedValueOnce(listPayload([]));
    const empty = await mountOpen();
    expect(empty.text()).toContain("本回合还没有请求快照");
    empty.unmount();

    api.list.mockResolvedValueOnce(listPayload([], true));
    const expired = await mountOpen();
    expect(expired.text()).toContain("快照已过期");
    expect(expired.text()).not.toContain("本回合还没有请求快照");
    expired.unmount();
  });

  it("does not fetch while the panel is closed", async () => {
    const wrapper = await mountOpen({ modelValue: false });
    expect(api.list).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("switches the detail when a rail item is clicked", async () => {
    const wrapper = await mountOpen();
    const rail = wrapper.findAll(".rail-item");
    expect(rail).toHaveLength(3);
    expect(wrapper.find(".detail-title").text()).toContain("step 1");

    await rail[1].trigger("click");
    await flushPromises();

    expect(wrapper.find(".detail-title").text()).toContain("step 2");
    expect(rail[1].classes()).toContain("is-active");
    wrapper.unmount();
  });

  it("groups the rail into this turn and auxiliary calls", async () => {
    const wrapper = await mountOpen();
    const groups = wrapper.findAll(".rail-group-title").map((node) => node.text());
    expect(groups).toEqual(["本回合", "本会话 · 辅助"]);
    const aux = wrapper.findAll(".rail-group")[1];
    expect(aux.text()).toContain("压缩摘要");
    wrapper.unmount();
  });

  it("renders rail badges for cache, compression, retry and tools", async () => {
    const wrapper = await mountOpen();
    const rail = wrapper.findAll(".rail-item");
    expect(rail[0].text()).toContain("缓存 10%");
    expect(rail[1].text()).toContain("压缩");
    expect(rail[1].text()).toContain("retry");
    expect(rail[1].text()).toContain("+1 tool");
    wrapper.unmount();
  });

  it("collapses diagnostics and raw json by default", async () => {
    const wrapper = await mountOpen();
    expect(wrapper.find(".sec-diagnostics").classes()).toContain("collapsed");
    expect(wrapper.find(".sec-raw").classes()).toContain("collapsed");
    expect(wrapper.find(".raw-json").exists()).toBe(false);
    wrapper.unmount();
  });

  it("closes the panel from the header action", async () => {
    const wrapper = await mountOpen();
    await wrapper.find(".inspector-close").trigger("click");
    expect(wrapper.emitted("update:modelValue")).toEqual([[false]]);
    wrapper.unmount();
  });

  it("only shows the mode switch when the account can edit", async () => {
    const readOnly = await mountOpen({ canEdit: false });
    expect(readOnly.find(".devmode-switch").exists()).toBe(false);
    readOnly.unmount();

    const editor = await mountOpen({ canEdit: true });
    const toggle = editor.find(".devmode-switch");
    expect(toggle.exists()).toBe(true);
    await toggle.trigger("click");
    expect(editor.emitted("toggle")).toHaveLength(1);
    expect(editor.emitted("update:modelValue")).toBeUndefined();
    editor.unmount();
  });

  it("loads the requested turn on open and re-runs the list on refresh", async () => {
    const wrapper = await mountOpen({ initialTurnId: "turn-9" });
    expect(api.list).toHaveBeenCalledWith(
      "s1",
      expect.objectContaining({ turn_id: "turn-9" }),
    );

    await wrapper.find(".inspector-refresh").trigger("click");
    await flushPromises();
    expect(api.list).toHaveBeenCalledTimes(2);
    expect(api.list).toHaveBeenLastCalledWith(
      "s1",
      expect.objectContaining({ turn_id: "turn-9" }),
    );
    wrapper.unmount();
  });

  it("shows the per-session byte budget in the limits line", async () => {
    const wrapper = await mountOpen();
    const limits = wrapper.find(".inspector-limits");
    expect(limits.text()).toContain("每会话最多 128 条");
    expect(limits.text()).toContain("16.0 MB");
    expect(limits.text()).not.toContain("512 KB");
    wrapper.unmount();
  });

  it("reloads the selected detail past the cache from the header action", async () => {
    const wrapper = await mountOpen();
    expect(api.get).toHaveBeenCalledTimes(1);
    const snapshotId = api.get.mock.calls[0][0];

    await wrapper.find(".action-refresh").trigger("click");
    await flushPromises();
    await flushPromises();

    expect(api.get).toHaveBeenCalledTimes(2);
    expect(api.get).toHaveBeenLastCalledWith(snapshotId);
    expect(wrapper.find(".rail-item.is-active").text()).toContain("step 1");
    expect(wrapper.find(".detail-title").text()).toContain("step 1");
    wrapper.unmount();
  });

  it("resets detail interaction state when switching snapshots", async () => {
    const wrapper = await mountOpen();
    expect(wrapper.findAll(".tool-row")).toHaveLength(1);

    await wrapper.find(".tool-search").setValue("nothing-matches");
    expect(wrapper.findAll(".tool-row")).toHaveLength(0);

    await wrapper.findAll(".rail-item")[1].trigger("click");
    await flushPromises();

    const search = wrapper.find(".tool-search")
      .element as HTMLInputElement;
    expect(search.value).toBe("");
    expect(wrapper.findAll(".tool-row")).toHaveLength(1);
    expect(wrapper.find(".detail-title").text()).toContain("step 2");
    wrapper.unmount();
  });

  it("closes from the narrow-screen backdrop and keeps the escape path", async () => {
    const wrapper = await mountOpen();
    const backdrop = wrapper.find(".devmode-backdrop");
    expect(backdrop.exists()).toBe(true);
    await backdrop.trigger("click");
    expect(wrapper.emitted("update:modelValue")).toEqual([[false]]);

    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    expect(wrapper.emitted("update:modelValue")).toHaveLength(2);
    wrapper.unmount();
  });
});
