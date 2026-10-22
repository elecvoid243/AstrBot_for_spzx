import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { enableAutoUnmount, flushPromises, mount } from "@vue/test-utils";
import MockAdapter from "axios-mock-adapter";
import { createVuetify } from "vuetify";
import * as components from "vuetify/components";
import ThreadPanel from "./ThreadPanel.vue";
import ChatMessageList from "./ChatMessageList.vue";
import {
  CANCELLED_STORAGE_KEY,
  IGNORED_STORAGE_KEY,
  STORAGE_KEY,
  SUBMISSION_STORAGE_KEY,
  useInteractiveChoiceStore,
} from "@/stores/interactiveChoice";
import { httpClient } from "@/api/http";
import type { ChatRecord, ChatThread } from "@/composables/useMessages";
import type { InteractiveChoicePart } from "@/composables/parseInteractiveChoice";

const MAIN_UMO = "webchat:FriendMessage:webchat!user!main";
const CHOICE_STORAGE_KEYS = [
  STORAGE_KEY,
  SUBMISSION_STORAGE_KEY,
  IGNORED_STORAGE_KEY,
  CANCELLED_STORAGE_KEY,
];
const CHOICE: InteractiveChoicePart = {
  type: "interactive_choice",
  request_id: "bc6bb8b4-8fa5-42dd-889c-86e3c6f3815a",
  prompt: "Choose a thread option",
  options: [
    { id: "a", label: "Option A" },
    { id: "b", label: "Option B" },
  ],
};
const THREAD: ChatThread = {
  thread_id: "thread-a",
  parent_session_id: "main",
  parent_message_id: 1,
  base_checkpoint_id: "",
  selected_text: "Selected text",
  created_at: "2026-10-23T00:00:00Z",
  updated_at: "2026-10-23T00:00:00Z",
};
const vuetify = createVuetify({ components });
let api: MockAdapter;
enableAutoUnmount(afterEach);

/**
 * Build history through the message list's public record contract.
 *
 * Args:
 *   part: Choice prompt or persisted terminal history to display.
 *
 * Returns:
 *   A bot history record carrying the supplied choice.
 */
function choiceRecord(part: InteractiveChoicePart = CHOICE): ChatRecord {
  return { id: part.request_id, content: { type: "bot", message: [part] } };
}

beforeEach(() => {
  localStorage.clear();
  setActivePinia(createPinia());
  api = new MockAdapter(httpClient);
  api.onPost("/api/chat/interactive-choice/pending").reply(200, {
    status: "ok",
    data: { pending: [] },
  });
});

afterEach(() => api.restore());

describe("message lists with the real session-scoped choice store", () => {
  it.each([false, true])(
    "renders ordinary history without a live UMO, sideChat=%s",
    async (sideChat) => {
      api.onGet("/api/v1/chat/threads/thread-a").reply(200, {
        status: "ok",
        data: {
          history: [
            {
              id: 1,
              content: {
                type: "bot",
                message: [{ type: "plain", text: "Thread reply" }],
              },
            },
          ],
        },
      });
      const store = useInteractiveChoiceStore();
      store.hydrate(MAIN_UMO);
      const errors: unknown[] = [];
      const wrapper = mount(ThreadPanel, {
        props: { modelValue: true, thread: THREAD, isDark: false, sideChat },
        global: {
          plugins: [vuetify],
          config: { errorHandler: (error) => errors.push(error) },
        },
      });
      await flushPromises();
      expect(wrapper.findComponent(ChatMessageList).exists()).toBe(true);
      expect(wrapper.text()).toContain("Thread reply");
      expect(errors).toEqual([]);
      expect(store.currentUmo).toBe(MAIN_UMO);
      expect(api.history.post).toEqual([]);
    },
  );

  it.each([false, true])(
    "loads choice history without touching the main session, sideChat=%s",
    async (sideChat) => {
      let resolveHistory!: () => void;
      const historyReady = new Promise<void>((resolve) => {
        resolveHistory = resolve;
      });
      api.onGet("/api/v1/chat/threads/thread-a").reply(async () => {
        await historyReady;
        return [
          200,
          {
            status: "ok",
            data: {
              history: [
                {
                  id: 1,
                  content: {
                    type: "bot",
                    message: [
                      { type: "plain", text: "Asynchronous thread reply" },
                      CHOICE,
                      {
                        ...CHOICE,
                        request_id: "answered",
                        prompt: "Answered prompt",
                        answer: { choice_id: "a" },
                      },
                      {
                        ...CHOICE,
                        request_id: "cancelled",
                        prompt: "Cancelled prompt",
                        resolved: { reason: "cancelled" },
                      },
                    ],
                  },
                },
              ],
            },
          },
        ];
      });
      const store = useInteractiveChoiceStore();
      store.hydrate(MAIN_UMO);
      store.addChoice(MAIN_UMO, {
        ...CHOICE,
        request_id: "main-only",
        prompt: "Main-only prompt",
      });
      // A colliding request ID must not lend this transcript a local answer.
      store.markSubmitted(MAIN_UMO, CHOICE.request_id, "option", {
        optionId: "b",
      });
      store.markIgnored(MAIN_UMO, [CHOICE.request_id]);
      store.markCancelled(MAIN_UMO, CHOICE.request_id);
      const stateBefore = JSON.stringify(store.$state);
      const persistenceBefore = CHOICE_STORAGE_KEYS.map((key) =>
        localStorage.getItem(key),
      );
      expect(persistenceBefore.every((value) => value !== null)).toBe(true);
      const errors: unknown[] = [];
      const wrapper = mount(ThreadPanel, {
        props: { modelValue: true, thread: THREAD, isDark: false, sideChat },
        global: {
          plugins: [vuetify],
          config: { errorHandler: (error) => errors.push(error) },
        },
      });
      await flushPromises();
      expect(wrapper.findAll(".message-row")).toHaveLength(0);
      expect(JSON.stringify(store.$state)).toBe(stateBefore);
      resolveHistory();
      await flushPromises();
      expect(errors).toEqual([]);
      expect(wrapper.text()).toContain("Asynchronous thread reply");
      expect(wrapper.text()).toContain(CHOICE.prompt);
      expect(wrapper.text()).not.toContain("Main-only prompt");
      const boxes = wrapper.findAll(".interactive-choice-box");
      expect(boxes).toHaveLength(3);
      expect(boxes[0].classes()).not.toContain("is-submitted");
      expect(boxes[0].classes()).not.toContain("is-ignored");
      expect(boxes[0].classes()).not.toContain("is-cancelled");
      expect(boxes[1].find(".choice-result-value").text()).toBe("a. Option A");
      expect(boxes[2].classes()).toContain("is-cancelled");
      await boxes[0].get(".choice-collapse-toggle").trigger("click");
      expect(boxes[0].text()).toContain("Option A");
      expect(
        wrapper
          .find(
            ".choice-option-button, .choice-input, .choice-submit-button, .choice-cancel-button",
          )
          .exists(),
      ).toBe(false);
      expect(errors).toEqual([]);
      expect(JSON.stringify(store.$state)).toBe(stateBefore);
      expect(
        CHOICE_STORAGE_KEYS.map((key) => localStorage.getItem(key)),
      ).toEqual(persistenceBefore);
      expect(api.history.post).toEqual([]);
      expect(api.history.delete).toEqual([]);
    },
  );

  it("renders initial unscoped choice history with no hydrated store", async () => {
    const store = useInteractiveChoiceStore();
    const wrapper = mount(ChatMessageList, {
      props: { messages: [choiceRecord()] },
      global: { plugins: [vuetify] },
    });
    await flushPromises();
    expect(wrapper.text()).toContain(CHOICE.prompt);
    await wrapper.get(".choice-collapse-toggle").trigger("click");
    expect(wrapper.text()).toContain("Option A");
    expect(
      wrapper
        .find(".choice-option-button, .choice-input, .choice-cancel-button")
        .exists(),
    ).toBe(false);
    expect(store.currentUmo).toBeNull();
    expect(store.activeChoices).toEqual({});
    expect(localStorage.length).toBe(0);
    expect(api.history.post).toEqual([]);
  });

  it.each([false, true])(
    "keeps scoped choices live and submittable after history loading, asynchronous=%s",
    async (asynchronous) => {
      api.onPost("/api/chat/interactive-choice/pending").reply(200, {
        status: "ok",
        data: { pending: [CHOICE] },
      });
      api
        .onPost(`/api/chat/interactive-choice/${CHOICE.request_id}`)
        .reply(200, { status: "ok" });
      const wrapper = mount(ChatMessageList, {
        props: {
          messages: asynchronous ? [] : [choiceRecord()],
          currentUmo: MAIN_UMO,
        },
        global: { plugins: [vuetify] },
      });
      await flushPromises();
      if (asynchronous) {
        await wrapper.setProps({ messages: [choiceRecord()] });
        await flushPromises();
      }
      const store = useInteractiveChoiceStore();
      expect(store.currentUmo).toBe(MAIN_UMO);
      expect(store.activeChoices[MAIN_UMO][CHOICE.request_id]).toEqual(CHOICE);
      expect(
        api.history.post
          .filter((request) => request.url?.endsWith("/pending"))
          .map((request) => JSON.parse(request.data)),
      ).toEqual(Array(asynchronous ? 2 : 1).fill({ session_id: MAIN_UMO }));
      await wrapper.get(".choice-option-button").trigger("click");
      await flushPromises();
      expect(wrapper.get(".choice-result-value").text()).toBe("a. Option A");
      expect(JSON.parse(api.history.post.at(-1)!.data)).toEqual({
        choice_id: "a",
        free_text: "",
      });
      expect(
        store.getSubmissionState(MAIN_UMO, CHOICE.request_id)?.optionId,
      ).toBe("a");
      expect(
        store.activeChoices[MAIN_UMO]?.[CHOICE.request_id],
      ).toBeUndefined();
    },
  );

  it.each(["answered", "ignored", "cancelled"])(
    "restores scoped %s history after refresh",
    async (terminal) => {
      const store = useInteractiveChoiceStore();
      store.hydrate(MAIN_UMO);
      if (terminal === "answered")
        store.markSubmitted(MAIN_UMO, CHOICE.request_id, "option", {
          optionId: "b",
        });
      if (terminal === "ignored")
        store.markIgnored(MAIN_UMO, [CHOICE.request_id]);
      if (terminal === "cancelled")
        store.markCancelled(MAIN_UMO, CHOICE.request_id);
      store.$reset();
      const wrapper = mount(ChatMessageList, {
        props: { messages: [choiceRecord()], currentUmo: MAIN_UMO },
        global: { plugins: [vuetify] },
      });
      await flushPromises();
      const box = wrapper.get(".interactive-choice-box");
      expect(box.classes()).toContain(
        terminal === "answered" ? "is-submitted" : `is-${terminal}`,
      );
      if (terminal === "answered")
        expect(box.get(".choice-result-value").text()).toBe("b. Option B");
      expect(
        wrapper
          .find(".choice-option-button, .choice-input, .choice-cancel-button")
          .exists(),
      ).toBe(false);
      expect(store.currentUmo).toBe(MAIN_UMO);
      expect(
        store.activeChoices[MAIN_UMO]?.[CHOICE.request_id],
      ).toBeUndefined();
    },
  );

  it("switches real scopes and leaves the active bucket intact when scope is removed", async () => {
    const nextUmo = "webchat:FriendMessage:webchat!user!next";
    const nextChoice = {
      ...CHOICE,
      request_id: "next-choice",
      prompt: "Next session prompt",
    };
    api.onPost("/api/chat/interactive-choice/pending").reply((request) => [
      200,
      {
        status: "ok",
        data: {
          pending:
            JSON.parse(request.data).session_id === MAIN_UMO
              ? [CHOICE]
              : [nextChoice],
        },
      },
    ]);
    const wrapper = mount(ChatMessageList, {
      props: { messages: [choiceRecord()], currentUmo: MAIN_UMO },
      global: { plugins: [vuetify] },
    });
    await flushPromises();
    await wrapper.setProps({
      messages: [choiceRecord(nextChoice)],
      currentUmo: nextUmo,
    });
    await flushPromises();
    const store = useInteractiveChoiceStore();
    expect(store.currentUmo).toBe(nextUmo);
    expect(wrapper.text()).toContain(nextChoice.prompt);
    expect(wrapper.text()).not.toContain(CHOICE.prompt);
    expect(store.activeChoices[MAIN_UMO]).toBeUndefined();
    expect(store.activeChoices[nextUmo][nextChoice.request_id]).toEqual(
      nextChoice,
    );
    const stateBefore = JSON.stringify(store.$state);
    const persistenceBefore = CHOICE_STORAGE_KEYS.map((key) =>
      localStorage.getItem(key),
    );
    const requestsBefore = api.history.post.length;
    await wrapper.setProps({ currentUmo: "", messages: [choiceRecord()] });
    await flushPromises();
    expect(wrapper.text()).toContain(CHOICE.prompt);
    expect(wrapper.text()).not.toContain(nextChoice.prompt);
    expect(
      wrapper
        .find(".choice-option-button, .choice-input, .choice-cancel-button")
        .exists(),
    ).toBe(false);
    expect(JSON.stringify(store.$state)).toBe(stateBefore);
    expect(CHOICE_STORAGE_KEYS.map((key) => localStorage.getItem(key))).toEqual(
      persistenceBefore,
    );
    expect(api.history.post).toHaveLength(requestsBefore);
  });
});
