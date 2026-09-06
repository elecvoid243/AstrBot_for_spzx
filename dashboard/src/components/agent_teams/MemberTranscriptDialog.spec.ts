// Author: elecvoid243
// Date: 2026-09-05
// Plan: docs/superpowers/plans/2026-09-05-agent-teams-refinements-3.md Task 7

import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import MemberTranscriptDialog from "./MemberTranscriptDialog.vue";

const detachSpy = vi.fn();
const attachSpy = vi.fn(() => ({
  record: { content: { message: [], isLoading: false } },
  userBubbles: { value: [] },
  detach: detachSpy,
}));

vi.mock("@/composables/useMemberRunStream", () => ({
  useMemberRunStream: () => ({ attach: attachSpy }),
}));

// The choice box owns submission state (store-backed); stub it so the dialog
// spec asserts wiring (part + umo) rather than the box's internals.
const choiceBoxStub = {
  props: ["part", "umo"],
  template: '<div class="choice-box-stub" :data-umo="umo" :data-request="part.request_id" />',
};

const stubs = {
  "v-dialog": { props: ["modelValue", "fullscreen"], template: "<div class=\"v-dialog\" :class=\"{ 'is-fullscreen': fullscreen }\"><slot /></div>" },
  "v-card": { template: '<div><slot /></div>' },
  "v-card-title": { template: '<div><slot /></div>' },
  "v-card-text": { template: '<div><slot /></div>' },
  "v-card-actions": { template: '<div><slot /></div>' },
  "v-divider": { template: "<hr />" },
  "v-spacer": { template: "<span />" },
  "v-icon": { template: '<i><slot /></i>' },
  "v-chip": { template: '<span><slot /></span>' },
  "v-btn": { template: "<button><slot /></button>" },
  "v-text-field": {
    props: ["modelValue"],
    template: '<input :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
  },
  InteractiveChoiceBox: choiceBoxStub,
};

function mountDialog(props: Record<string, unknown> = {}) {
  return mount(MemberTranscriptDialog, {
    props: {
      modelValue: true,
      runId: "run1",
      memberId: "m1",
      memberName: "Agent Alpha",
      umo: "webchat:FriendMessage:conv-1",
      timeline: [],
      ...props,
    },
    global: { stubs },
  });
}

describe("MemberTranscriptDialog", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it("renders timeline turn entries", () => {
    const wrapper = mountDialog({
      timeline: [
        { turnId: "t1", kind: "turn", direction: "sent", text: "task A", streaming: false },
        { turnId: "t2", kind: "turn", direction: "reply", text: "answer", streaming: false, parts: [] },
      ],
    });

    const turns = wrapper.findAll('[data-test="timeline-turn"]');
    expect(turns).toHaveLength(2);
    expect(turns[0].text()).toContain("task A");
    expect(turns[1].text()).toContain("answer");
  });

  it("renders a choice entry wired to the member umo", () => {
    const wrapper = mountDialog({
      timeline: [
        {
          turnId: "c1",
          kind: "choice",
          direction: "shown",
          text: "Pick one",
          parts: [
            { type: "interactive_choice", request_id: "req-1", prompt: "Pick one", options: [] },
          ],
        },
      ],
    });

    const entry = wrapper.find('[data-test="choice-entry"]');
    expect(entry.exists()).toBe(true);
    const box = wrapper.find(".choice-box-stub");
    expect(box.attributes("data-umo")).toBe("webchat:FriendMessage:conv-1");
    expect(box.attributes("data-request")).toBe("req-1");
  });

  it("renders system entries", () => {
    const wrapper = mountDialog({
      timeline: [{ turnId: "s1", kind: "system", text: "reply timeout" }],
    });
    expect(wrapper.find('[data-test="system-entry"]').text()).toContain("reply timeout");
  });

  it("emits send with the trimmed draft and clears the input", async () => {
    const wrapper = mountDialog();

    await wrapper.find('[data-test="input-field"]').setValue("  Hello  ");
    await wrapper.find('[data-test="send-button"]').trigger("click");

    expect(wrapper.emitted("send")).toEqual([["Hello"]]);
    expect((wrapper.find('[data-test="input-field"]').element as HTMLInputElement).value).toBe("");
  });

  it("does not emit send for a blank draft", async () => {
    const wrapper = mountDialog();
    await wrapper.find('[data-test="send-button"]').trigger("click");
    expect(wrapper.emitted("send")).toBeUndefined();
  });

  it("emits interrupt only after confirmation", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    const wrapper = mountDialog();

    await wrapper.find('[data-test="interrupt-button"]').trigger("click");
    expect(wrapper.emitted("interrupt")).toBeUndefined();

    confirmSpy.mockReturnValue(true);
    await wrapper.find('[data-test="interrupt-button"]').trigger("click");
    expect(wrapper.emitted("interrupt")).toHaveLength(1);

    confirmSpy.mockRestore();
  });

  it("toggles fullscreen from the header button", async () => {
    const wrapper = mountDialog();
    expect(wrapper.find(".v-dialog").classes()).not.toContain("is-fullscreen");

    await wrapper.find('[data-test="fullscreen-toggle"]').trigger("click");
    expect(wrapper.find(".v-dialog").classes()).toContain("is-fullscreen");
  });

  it("shows the load-more control only when more history exists", async () => {
    const wrapper = mountDialog();
    expect(wrapper.find('[data-test="load-more"]').exists()).toBe(false);

    await wrapper.setProps({ hasMore: true });
    await wrapper.find('[data-test="load-more"]').trigger("click");
    expect(wrapper.emitted("loadMore")).toHaveLength(1);
  });

  it("attaches the member run stream while open and detaches on close", async () => {
    const wrapper = mountDialog();
    expect(attachSpy).toHaveBeenCalledWith("webchat:FriendMessage:conv-1", "run1");

    await wrapper.setProps({ modelValue: false });
    expect(detachSpy).toHaveBeenCalled();
  });
});
