// Author: elecvoid243 @ 2026-10-22
// ThreadPanel: drag-to-resize, side-chat mode and close semantics.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { defineComponent, h, nextTick } from "vue";
import { flushPromises, mount } from "@vue/test-utils";

const { getThreadMock } = vi.hoisted(() => ({
  getThreadMock: vi.fn(),
}));

vi.mock("@/api/v1", () => ({
  chatApi: {
    getThread: getThreadMock,
    sendThreadMessageUrl: (threadId: string) => `/threads/${threadId}/messages`,
  },
}));

import ThreadPanel from "./ThreadPanel.vue";
import type { ChatThread } from "@/composables/useMessages";

const THREAD: ChatThread = {
  thread_id: "thread-1",
  parent_session_id: "session-1",
  parent_message_id: 0,
  base_checkpoint_id: "",
  selected_text: "被选中的那段文字",
  created_at: "2026-10-22T00:00:00+00:00",
  updated_at: "2026-10-22T00:00:00+00:00",
};

const VBtnStub = defineComponent({
  name: "VBtn",
  props: { icon: { type: String, default: "" } },
  emits: ["click"],
  setup(props, { emit }) {
    return () =>
      h("button", {
        class: "v-btn-stub",
        "data-icon": props.icon,
        onClick: () => emit("click"),
      });
  },
});

function mountPanel(
  props: Partial<{
    modelValue: boolean;
    thread: ChatThread;
    isDark: boolean;
    sideChat: boolean;
  }> = {},
) {
  return mount(ThreadPanel, {
    props: {
      modelValue: true,
      thread: THREAD,
      isDark: false,
      ...props,
    },
    global: {
      stubs: { ChatMessageList: true, VBtn: VBtnStub },
    },
  });
}

beforeEach(() => {
  getThreadMock.mockReset();
  getThreadMock.mockResolvedValue({ data: { data: { history: [] } } });
});

describe("ThreadPanel", () => {
  it("renders the side-chat header without the quote or delete button", async () => {
    const wrapper = mountPanel({ sideChat: true });
    await flushPromises();

    expect(wrapper.get(".thread-panel-title").text()).toBe("侧边聊天");
    expect(wrapper.get(".thread-panel-subtitle").text()).toContain(
      "关闭后销毁",
    );
    expect(wrapper.find(".thread-selected-text").exists()).toBe(false);
    expect(wrapper.find('[data-icon="mdi-delete-outline"]').exists()).toBe(
      false,
    );
  });

  it("keeps the quoted selection and delete button for branch threads", async () => {
    const wrapper = mountPanel();
    await flushPromises();

    expect(wrapper.get(".thread-panel-title").text()).toBe("分支");
    expect(wrapper.get(".thread-selected-text").text()).toBe(
      "被选中的那段文字",
    );
    expect(wrapper.find('[data-icon="mdi-delete-outline"]').exists()).toBe(
      true,
    );
  });

  it("resizes by dragging the left handle and clamps to [320, 1800]", async () => {
    const wrapper = mountPanel();
    await flushPromises();
    const panel = wrapper.get(".thread-panel");

    await wrapper.get(".thread-panel-resizer").trigger("mousedown");
    // happy-dom reports a zero rect, so the sidebar's right edge is 0.
    document.dispatchEvent(new MouseEvent("mousemove", { clientX: -600 }));
    await nextTick();
    expect(panel.attributes("style")).toContain("width: 600px");

    document.dispatchEvent(new MouseEvent("mousemove", { clientX: -9999 }));
    await nextTick();
    expect(panel.attributes("style")).toContain("width: 1800px");

    document.dispatchEvent(new MouseEvent("mousemove", { clientX: 900 }));
    await nextTick();
    expect(panel.attributes("style")).toContain("width: 320px");

    document.dispatchEvent(new MouseEvent("mouseup"));
  });

  it("emits update:modelValue=false from the close button", async () => {
    const wrapper = mountPanel({ sideChat: true });
    await flushPromises();

    await wrapper.get('[data-icon="mdi-close"]').trigger("click");
    expect(wrapper.emitted("update:modelValue")).toEqual([[false]]);
  });

  it("emits delete for branch threads", async () => {
    const wrapper = mountPanel();
    await flushPromises();

    await wrapper.get('[data-icon="mdi-delete-outline"]').trigger("click");
    expect(wrapper.emitted("delete")?.[0]).toEqual([THREAD]);
  });
});
