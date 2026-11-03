import { flushPromises, mount } from "@vue/test-utils";
import { reactive } from "vue";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ChatWorkflowRunPage from "./ChatWorkflowRunPage.vue";

const api = vi.hoisted(() => ({
  connection: vi.fn(),
  status: vi.fn(),
  run: vi.fn(),
  answer: vi.fn(),
  preview: vi.fn(),
  file: vi.fn(),
  resultFile: vi.fn(),
}));
vi.mock("@/api/chatRun", () => ({ chatRunApi: api }));
const testRoute = reactive({ query: { workflow_run: "root-run" } });
vi.mock("vue-router", () => ({
  useRoute: () => testRoute,
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
    testRoute.query.workflow_run = "root-run";
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
          artifact_ref: null,
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
  it("shows a dynamic choice and submits the original choice identity", async () => {
    api.connection.mockResolvedValue({
      session_id: "nonce",
      token: "secret",
      command: "/asset connect nonce",
      expires_at: 2000000000,
    });
    api.status.mockResolvedValue({ confirmed: true });
    const run = {
      run_id: "root-run",
      status: "running",
      control_status: "active",
      result: null,
      result_files: [],
      todos: [
        {
          todo_id: "choice:one",
          kind: "question",
          source_kind: "tool_call",
          question: "Which report?",
          constraints: { choices: ["brief", "detail"], max_length: 10000 },
          choice_spec: {
            options: [
              { id: "brief", label: "Brief" },
              {
                id: "detail",
                label: "Detailed",
                description: "Include evidence",
              },
            ],
          },
          recommendation: { value: "detail", reason: "Evidence is available" },
          artifact_ref: null,
          status: "pending",
        },
      ],
    };
    api.run.mockResolvedValue(run);
    api.answer.mockResolvedValue(run);
    const wrapper = mount(ChatWorkflowRunPage, { global: { stubs } });
    try {
      await flushPromises();
      expect(wrapper.text()).toContain("Detailed");
      expect(wrapper.text()).toContain("Evidence is available");
      await wrapper.get('input[value="detail"]').setValue();
      await wrapper.get('[data-testid="human-submit"]').trigger("click");
      await flushPromises();
      expect(api.answer).toHaveBeenCalledWith(
        "root-run",
        "nonce.secret",
        "choice:one",
        expect.objectContaining({ action: "answer", choice_id: "detail" }),
      );
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
  it("connects the next run while an answer to the previous run is still pending", async () => {
    api.connection.mockImplementation(async (runId: string) => ({
      session_id: "session-" + runId,
      token: "secret",
      command: "/asset connect " + runId,
      expires_at: 2000000000,
    }));
    api.status.mockResolvedValue({ confirmed: true });
    api.run.mockImplementation(async (runId: string) => ({
      run_id: runId,
      status: "running",
      control_status: "active",
      result: null,
      todos: [
        {
          todo_id: "todo-" + runId,
          kind: "question",
          question: "Region for " + runId,
          constraints: { choices: ["Asia", "Europe"] },
          artifact_ref: null,
          status: "pending",
          response: null,
        },
      ],
    }));
    let finishAnswer: ((value: unknown) => void) | undefined;
    api.answer.mockImplementation(
      () =>
        new Promise((resolve) => {
          finishAnswer = resolve;
        }),
    );
    const wrapper = mount(ChatWorkflowRunPage, { global: { stubs } });
    try {
      await flushPromises();
      await wrapper.get('[data-testid="human-answer"]').setValue("Asia");
      await wrapper.get('[data-testid="human-submit"]').trigger("click");
      expect(api.answer).toHaveBeenCalledWith(
        "root-run",
        "session-root-run.secret",
        "todo-root-run",
        expect.any(Object),
      );
      testRoute.query.workflow_run = "next-run";
      await flushPromises();
      expect(api.connection).toHaveBeenCalledWith("next-run");
      expect(
        wrapper.get('[data-testid="chat-refresh"]').attributes("disabled"),
      ).toBeUndefined();
      expect(api.run).toHaveBeenCalledWith(
        "next-run",
        "session-next-run.secret",
      );
      finishAnswer?.({
        run_id: "root-run",
        status: "completed",
        result: { text: "Old result" },
        todos: [],
      });
      await flushPromises();
      expect(wrapper.text()).toContain("Region for next-run");
      expect(wrapper.text()).not.toContain("Old result");
      await wrapper.get('[data-testid="human-answer"]').setValue("Europe");
      expect(
        wrapper.get('[data-testid="human-submit"]').attributes("disabled"),
      ).toBeUndefined();
    } finally {
      wrapper.unmount();
    }
  });
  it.each(["text/plain", "text/html"])(
    "previews %s as text and downloads the published file without an approval todo",
    async (type) => {
      const content =
        "<script>window.finalInjected = true</script>COMPLETE_FINAL_FILE_CONTENT";
      api.connection.mockResolvedValue({
        session_id: "nonce",
        token: "browser-secret",
        command: "/asset connect nonce",
        expires_at: 2000000000,
      });
      api.status.mockResolvedValue({ confirmed: true });
      api.run.mockResolvedValue({
        run_id: "root-run",
        status: "completed",
        control_status: "active",
        result: {
          text: "The report is attached.",
          files: [{ file_id: "published" }],
        },
        todos: [],
        result_files: [
          {
            file_id: "published",
            filename: "final-report.txt",
            media_type: type,
            artifact_id: "root-run",
            version: 1,
          },
        ],
      });
      const blob = new Blob([content], { type });
      api.resultFile.mockResolvedValue({ data: blob });
      const createUrl = vi
        .spyOn(URL, "createObjectURL")
        .mockReturnValue("blob:final-result");
      const names: string[] = [];
      const download = vi
        .spyOn(HTMLAnchorElement.prototype, "click")
        .mockImplementation(function (this: HTMLAnchorElement) {
          names.push(this.download);
        });
      const wrapper = mount(ChatWorkflowRunPage, { global: { stubs } });
      try {
        await flushPromises();
        expect(wrapper.find('[data-testid="human-todo"]').exists()).toBe(false);
        await wrapper
          .get('[data-testid="chat-result-preview"]')
          .trigger("click");
        await flushPromises();
        expect(api.resultFile).toHaveBeenLastCalledWith(
          "root-run",
          "nonce.browser-secret",
          "published",
          false,
        );
        expect(
          wrapper.get('[data-testid="chat-file-preview"]').text(),
        ).toContain(content);
        expect(wrapper.find("script").exists()).toBe(false);
        expect(wrapper.find("iframe").exists()).toBe(false);
        await wrapper
          .get('[data-testid="chat-result-download"]')
          .trigger("click");
        await flushPromises();
        expect(api.resultFile).toHaveBeenLastCalledWith(
          "root-run",
          "nonce.browser-secret",
          "published",
          true,
        );
        expect(createUrl).toHaveBeenCalledWith(blob);
        expect(names).toEqual(["final-report.txt"]);
      } finally {
        wrapper.unmount();
        download.mockRestore();
        createUrl.mockRestore();
      }
    },
  );
});
