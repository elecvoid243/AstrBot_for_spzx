// Author: impl_task10
// Date: 2026-11-16
//
// Detail contract for one developer-mode snapshot: the fixed section order,
// provenance attribution (including the approximate marker), tool filtering
// and schema expansion, long-message expansion, the injection panel hand-off
// and the copy/raw interactions.

import { afterEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { nextTick } from "vue";
import LlmSnapshotDetailComponent from "@/components/chat/LlmSnapshotDetail.vue";
import type { LlmSnapshotDetail } from "@/composables/llmRequestSnapshots";

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
  "devMode.notice.memory":
    "快照仅保存在内存 · 2 小时后过期 · 关闭开发者模式即停止采集",
  "devMode.notice.redacted": "已脱敏 {count} 处",
  "devMode.action.copy": "复制 JSON",
  "devMode.action.download": "下载",
  "devMode.action.refresh": "刷新",
  "devMode.action.close": "关闭",
  "devMode.settings.title": "开发者模式",
  "devMode.settings.switch": "记录 LLM 请求快照",
  "devMode.settings.limits": "每会话最多 {count} 条 · {size} · {ttl} 后过期",
  "devMode.group.turn": "本回合",
  "devMode.group.aux": "本会话 · 辅助",
  "devMode.rail.compression": "压缩",
  "devMode.truncated": "已截断，原始大小 {size}",
  "devMode.provenance.approx": "近似定位",
  "devMode.copy.failed": "复制失败",
  "devMode.tool.searchPlaceholder": "搜索工具名称、描述或来源",
  "devMode.tool.empty": "没有匹配的工具",
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

const LONG_MESSAGE = `${"A".repeat(400)}TAIL-MARKER`;

function snapshot(overrides: Record<string, unknown> = {}): LlmSnapshotDetail {
  return {
    envelope: {
      snapshot_id: "snap-2",
      session_id: "s1",
      kind: "chat",
      stage: "main",
      step: 2,
      status: "ok",
      created_at: 1763200867.123,
      duration_ms: 8300,
      bytes: 21606,
      original_bytes: 99000,
      truncated: true,
      redactions: 3,
      provider: {
        id: "deepseek-official",
        model: "deepseek-v4.1",
        host: "api.deepseek.com",
      },
      request: { attempt: 1, temperature: 0.7, max_tokens: 8192 },
      counts: {
        messages: 3,
        tools: 2,
        system_chars: 18,
        estimated_input_tokens: 26300,
        omitted_messages: 1,
      },
      response: {
        response_id: "chatcmpl-9f2a",
        finish_reason: "tool_calls",
        usage: {
          input_other: 21000,
          input_cached: 5300,
          output: 642,
          total: 26942,
        },
        usage_matches_request: true,
        ttft_ms: 820,
        chunks: 214,
      },
      tool_executions: [
        {
          call_id: "call_a1",
          name: "read_file",
          status: "ok",
          duration_ms: 312,
          result_bytes: 1840,
          result_preview: "file body",
        },
      ],
      diagnostics: {
        compression: {
          strategy: "llm_summary",
          tokens_before: 61200,
          tokens_after: 8400,
        },
        cache: {
          hit_ratio: 0.202,
          system_changed: false,
          tools_changed: true,
          divergence_index: 6,
        },
        retries: { count: 1, fallback: false },
        stop_reason: "completed",
        omitted_messages: 1,
      },
      provenance: [
        { label: "persona", chars: 6350, approx: false },
        { label: "skills", chars: 1840, approx: true },
      ],
      injections: [
        {
          plugin: "tc_memory",
          handler: "decorate_llm_req",
          changes: [
            {
              field: "system_prompt",
              delta: 412,
              lossy: false,
              preview: "## memory",
            },
          ],
        },
      ],
      tool_meta: {
        read_file: { origin: "builtin", denied: false, background: false },
      },
      ...overrides,
    },
    raw: {
      model: "deepseek-v4.1",
      messages: [{ role: "user", content: "hello" }],
    },
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
          content: [{ type: "text", text: LONG_MESSAGE }],
          chars: LONG_MESSAGE.length,
          estimated_tokens: 410,
        },
        {
          index: 2,
          role: "assistant",
          content: [{ type: "text", text: "calling read_file" }],
          tool_calls: [{ id: "call_a1", name: "read_file" }],
          chars: 17,
          estimated_tokens: 9,
        },
      ],
      tools: [
        {
          name: "read_file",
          description: "Read a text file from the workspace.",
          parameters: {
            type: "object",
            properties: { path: { type: "string" } },
          },
          origin: "builtin",
          active: true,
          denied: false,
          schema_bytes: 120,
        },
        {
          name: "shell",
          description: "Run a shell command in the host.",
          parameters: { type: "object", properties: {} },
          origin: "plugin:shell",
          active: true,
          denied: true,
          schema_bytes: 80,
        },
      ],
      params: { temperature: 0.7, max_tokens: 8192 },
      extra: {},
    },
  } as unknown as LlmSnapshotDetail;
}

function mountDetail(props: {
  snapshot: LlmSnapshotDetail;
  previous?: LlmSnapshotDetail;
}) {
  return mount(LlmSnapshotDetailComponent, { props });
}

afterEach(() => {
  vi.useRealTimers();
});

describe("LlmSnapshotDetail", () => {
  it("renders overview, response, system, tools, messages and injections", () => {
    const wrapper = mountDetail({ snapshot: snapshot() });

    expect(wrapper.find(".sec-overview").exists()).toBe(true);
    expect(wrapper.find(".sec-overview").text()).toContain("deepseek-v4.1");
    expect(wrapper.find(".sec-overview").text()).toContain("api.deepseek.com");

    expect(wrapper.find(".sec-response").text()).toContain("tool_calls");
    expect(wrapper.find(".sec-response").text()).toContain("26942");
    expect(wrapper.find(".sec-response").text()).toContain("820 ms");
    expect(wrapper.find(".tool-exec-row").text()).toContain("read_file");
    expect(wrapper.find(".tool-exec-row").text()).toContain("ok");

    expect(wrapper.find(".sec-system").text()).toContain("You are AstrBot.");
    expect(wrapper.find(".sec-tools").text()).toContain("read_file");
    expect(wrapper.find(".sec-messages").text()).toContain("user");
    expect(wrapper.find(".sec-injections .injection-panel").exists()).toBe(true);
    expect(wrapper.find(".sec-injections").text()).toContain("tc_memory");
  });

  it("marks approximate provenance with a triangle and shows the truncated badge", () => {
    const wrapper = mountDetail({ snapshot: snapshot() });
    const chips = wrapper.findAll(".prov-chip");
    expect(chips).toHaveLength(2);
    expect(chips[0].classes()).not.toContain("is-approx");
    expect(chips[1].classes()).toContain("is-approx");
    expect(chips[1].text()).toContain("△");
    expect(wrapper.find(".badge-truncated").text()).toContain("已截断");
  });

  it("filters tools by name, description and origin", async () => {
    const wrapper = mountDetail({ snapshot: snapshot() });
    const search = wrapper.find(".tool-search");
    expect(wrapper.findAll(".tool-row")).toHaveLength(2);

    await search.setValue("shell");
    expect(wrapper.findAll(".tool-row")).toHaveLength(1);
    expect(wrapper.findAll(".tool-row")[0].text()).toContain("shell");

    await search.setValue("workspace");
    expect(wrapper.findAll(".tool-row")).toHaveLength(1);
    expect(wrapper.findAll(".tool-row")[0].text()).toContain("read_file");

    await search.setValue("plugin:shell");
    expect(wrapper.findAll(".tool-row")).toHaveLength(1);
    expect(wrapper.findAll(".tool-row")[0].text()).toContain("shell");

    await search.setValue("nothing-matches");
    expect(wrapper.findAll(".tool-row")).toHaveLength(0);
    expect(wrapper.find(".tool-empty").text()).toBe("没有匹配的工具");
  });

  it("expands a tool schema row", async () => {
    const wrapper = mountDetail({ snapshot: snapshot() });
    expect(wrapper.find(".tool-schema").exists()).toBe(false);
    await wrapper.findAll(".tool-row-head")[0].trigger("click");
    expect(wrapper.find(".tool-schema").text()).toContain('"path"');
    expect(
      wrapper.findAll(".tool-row-head")[0].attributes("aria-expanded"),
    ).toBe("true");
  });

  it("expands a long message in place", async () => {
    const wrapper = mountDetail({ snapshot: snapshot() });
    expect(wrapper.text()).not.toContain("TAIL-MARKER");
    const heads = wrapper.findAll(".message-row-head");
    await heads[1].trigger("click");
    expect(wrapper.find(".message-text").text()).toContain("TAIL-MARKER");
    expect(heads[1].attributes("aria-expanded")).toBe("true");
  });

  it("keeps diagnostics and raw json collapsed until toggled", async () => {
    const wrapper = mountDetail({ snapshot: snapshot() });
    expect(wrapper.find(".sec-diagnostics").classes()).toContain("collapsed");
    expect(wrapper.find(".sec-raw").classes()).toContain("collapsed");
    expect(wrapper.find(".raw-json").exists()).toBe(false);

    await wrapper.find(".sec-raw .sec-head").trigger("click");
    expect(wrapper.find(".raw-json").text()).toContain("deepseek-v4.1");

    await wrapper.find(".sec-diagnostics .sec-head").trigger("click");
    expect(wrapper.find(".sec-diagnostics").classes()).not.toContain(
      "collapsed",
    );
  });

  it("copies the detail json and resets the 已复制 feedback after 1.2s", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });

    const wrapper = mountDetail({ snapshot: snapshot() });
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });

    await wrapper.find(".action-copy").trigger("click");
    await Promise.resolve();
    await Promise.resolve();
    await nextTick();

    expect(writeText).toHaveBeenCalledTimes(1);
    expect(String(writeText.mock.calls[0][0])).toContain("deepseek-v4.1");
    expect(wrapper.find(".copy-feedback").text()).toBe("已复制");

    vi.advanceTimersByTime(1200);
    await nextTick();
    expect(wrapper.find(".copy-feedback").exists()).toBe(false);
  });

  it("renders the diff only from step two when a previous snapshot exists", () => {
    const current = snapshot();
    const withoutPrevious = mountDetail({ snapshot: current });
    expect(withoutPrevious.find(".sec-diff").exists()).toBe(false);

    const previous = snapshot({
      snapshot_id: "snap-1",
      step: 1,
      truncated: false,
    });
    const withPrevious = mountDetail({ snapshot: current, previous });
    expect(withPrevious.find(".sec-diff").exists()).toBe(true);
    expect(withPrevious.find(".sec-diff").text()).toContain("相对上一步");
  });

  it("exposes every section head as a button with aria-expanded", () => {
    const wrapper = mountDetail({ snapshot: snapshot() });
    const heads = wrapper.findAll(".sec-head");
    expect(heads.length).toBeGreaterThanOrEqual(8);
    for (const head of heads) {
      expect(head.element.tagName).toBe("BUTTON");
      expect(head.attributes("aria-expanded")).toBeTruthy();
    }
  });
});
