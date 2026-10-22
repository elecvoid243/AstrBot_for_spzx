// Author: elecvoid243 @ 2026-10-23
// Direct-send: the editor offers "Send now" only when a chat sender is
// registered, and emits the same payload as Save.

import { afterEach, describe, expect, it } from "vitest";
import { defineComponent, h } from "vue";
import { mount } from "@vue/test-utils";
import FileCommentEditor from "./FileCommentEditor.vue";
import { useFileComments } from "@/composables/useFileComments";

const VBtnStub = defineComponent({
  name: "VBtn",
  emits: ["click"],
  setup(_props, { slots, emit }) {
    return () =>
      h(
        "button",
        { class: "v-btn-stub", onClick: () => emit("click") },
        slots.default?.(),
      );
  },
});

function mountEditor() {
  return mount(FileCommentEditor, {
    props: {
      line: 46,
      commentId: null,
      initialText: "",
      lineContent: "mdi-close",
      contextBefore: null,
      contextAfter: null,
      filePath: "dashboard/scripts/subset-mdi-font.mjs",
    },
    global: {
      stubs: { VBtn: VBtnStub, VSpacer: true },
    },
  });
}

const cleanups: Array<() => void> = [];

afterEach(() => {
  for (const cleanup of cleanups.splice(0)) cleanup();
  useFileComments().resetForSession();
});

describe("FileCommentEditor direct send", () => {
  it("hides Send now when no chat sender is registered", () => {
    const wrapper = mountEditor();
    const labels = wrapper.findAll(".v-btn-stub").map((btn) => btn.text());
    expect(labels).toContain("保存");
    expect(labels).not.toContain("直接发送");
  });

  it("emits save-and-send with the comment payload", async () => {
    cleanups.push(useFileComments().onSendRequest(() => {}));
    const wrapper = mountEditor();
    await wrapper.get("textarea").setValue("  这段为什么删掉？ ");

    const send = wrapper
      .findAll(".v-btn-stub")
      .find((btn) => btn.text() === "直接发送");
    expect(send).toBeTruthy();
    await send!.trigger("click");

    const payload = wrapper.emitted("save-and-send")?.[0]?.[0] as {
      text: string;
      line: number;
    };
    expect(payload.text).toBe("这段为什么删掉？");
    expect(payload.line).toBe(46);
  });

  it("keeps Save separate from direct send", async () => {
    cleanups.push(useFileComments().onSendRequest(() => {}));
    const wrapper = mountEditor();
    await wrapper.get("textarea").setValue("保留在 chip 里");

    const save = wrapper
      .findAll(".v-btn-stub")
      .find((btn) => btn.text() === "保存");
    await save!.trigger("click");

    expect(wrapper.emitted("save")).toHaveLength(1);
    expect(wrapper.emitted("save-and-send")).toBeUndefined();
  });
});
