import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ChatWorkflowRunPage from "./ChatWorkflowRunPage.vue";

const api = vi.hoisted(() => ({
  connection: vi.fn(),
  status: vi.fn(),
  run: vi.fn(),
  answer: vi.fn(),
  preview: vi.fn(),
  file: vi.fn(),
}));
vi.mock("@/api/chatRun", () => ({ chatRunApi: api }));
vi.mock("vue-router", () => ({
  useRoute: () => ({ query: { workflow_run: "root-run" } }),
}));
vi.mock("@/i18n/composables", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/i18n/composables")>()),
  useModuleI18n: () => ({ tm: (key: string) => key }),
}));
const slot = { template: "<div><slot /></div>" };
const stubs = {
  LanguageSwitcher: true,
  VCard: slot,
  VCardTitle: slot,
  VCardText: slot,
  VCardActions: slot,
  VAlert: slot,
  VSpacer: slot,
  VBtn: {
    props: ["disabled", "loading"],
    template: '<button :disabled="disabled || loading"><slot /></button>',
  },
  VTextarea: {
    props: ["modelValue"],
    emits: ["update:modelValue"],
    template:
      '<textarea :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
  },
  VSelect: {
    props: ["modelValue", "items"],
    emits: ["update:modelValue"],
    template:
      '<select :value="modelValue" @change="$emit(\'update:modelValue\', $event.target.value)"><option value=""/><option v-for="item in items" :key="item" :value="item">{{ item }}</option></select>',
  },
};

describe("source-confirmed run details", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });
  it("connects this browser and answers the same persistent question without a Dashboard login", async () => {
    const todo = {
      todo_id: "persistent-todo",
      kind: "question",
      question: "Which region?",
      constraints: { choices: ["Asia", "Europe"], max_length: 100 },
      artifact_ref: null,
      status: "pending",
      response: null,
    };
    const waiting = {
      run_id: "root-run",
      status: "blocked",
      control_status: "active",
      todos: [todo],
      result: null,
    };
    api.connection.mockResolvedValue({
      session_id: "nonce",
      token: "browser-secret",
      command: "/asset connect nonce",
      expires_at: 2000000000,
    });
    api.status
      .mockResolvedValueOnce({ confirmed: false })
      .mockResolvedValue({ confirmed: true });
    api.run.mockResolvedValue(waiting);
    api.answer.mockResolvedValue({
      ...waiting,
      status: "completed",
      todos: [{ ...todo, status: "completed" }],
      result: { text: "Asia accepted" },
    });
    const wrapper = mount(ChatWorkflowRunPage, { global: { stubs } });
    try {
      await flushPromises();
      expect(
        wrapper.get('[data-testid="chat-connection-command"]').text(),
      ).toBe("/asset connect nonce");
      expect(wrapper.text()).not.toContain("browser-secret");
      expect(wrapper.find('[data-testid="human-todo"]').exists()).toBe(false);
      await wrapper.get('[data-testid="chat-refresh"]').trigger("click");
      await flushPromises();
      expect(api.run).toHaveBeenCalledWith("root-run", "nonce.browser-secret");
      await wrapper.get('[data-testid="human-answer"]').setValue("Asia");
      await wrapper.get('[data-testid="human-submit"]').trigger("click");
      await flushPromises();
      expect(api.answer).toHaveBeenCalledWith(
        "root-run",
        "nonce.browser-secret",
        "persistent-todo",
        {
          request_id: expect.any(String),
          action: "answer",
          answer: "Asia",
          feedback: null,
        },
      );
      expect(wrapper.get('[data-testid="chat-final-result"]').text()).toContain(
        "Asia accepted",
      );
      expect(wrapper.text()).not.toContain("task_run_id");
    } finally {
      wrapper.unmount();
    }
  });
  it("removes actionable content when the confirmed source grant is denied", async () => {
    localStorage.setItem("token", "unrelated-dashboard-session");
    api.connection.mockResolvedValue({
      session_id: "nonce",
      token: "browser-secret",
      command: "/asset connect nonce",
      expires_at: 2000000000,
    });
    api.status.mockResolvedValue({ confirmed: true });
    api.run.mockRejectedValue({
      response: { status: 403, data: { message: "Chat grant revoked" } },
    });
    const wrapper = mount(ChatWorkflowRunPage, { global: { stubs } });
    try {
      await flushPromises();
      expect(wrapper.get('[role="alert"]').text()).toContain(
        "Chat grant revoked",
      );
      expect(wrapper.find('[data-testid="human-todo"]').exists()).toBe(false);
      expect(api.answer).not.toHaveBeenCalled();
      expect(localStorage.getItem("token")).toBe("unrelated-dashboard-session");
    } finally {
      wrapper.unmount();
      localStorage.removeItem("token");
    }
  });
});
