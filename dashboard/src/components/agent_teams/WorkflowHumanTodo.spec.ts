import { flushPromises, mount } from "@vue/test-utils";
import { describe, it, expect, vi } from "vitest";
import WorkflowHumanTodo from "./WorkflowHumanTodo.vue";

const api = vi.hoisted(() => ({
  artifact: vi.fn(),
  export: vi.fn(),
  file: vi.fn(),
}));
vi.mock("@/api/workflowAssets", () => ({ workflowAssetsApi: api }));
vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({ tm: (key: string) => key }),
}));
const stubs = {
  "v-card": { template: "<div><slot /></div>" },
  "v-card-text": { template: "<div><slot /></div>" },
  "v-alert": { template: "<div><slot /></div>" },
  "v-btn": {
    props: ["disabled", "loading"],
    template: '<button :disabled="disabled || loading"><slot /></button>',
  },
  "v-textarea": {
    props: ["modelValue"],
    emits: ["update:modelValue"],
    template:
      '<textarea :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
  },
};
const todo = {
  todo_id: "todo",
  task_run_id: "task",
  question: "Approve?",
  kind: "approval",
  constraints: { choices: [], max_length: 100 },
  artifact_ref: { artifact_id: "report", version: 1 },
  status: "pending",
};

describe("persistent human request handling", () => {
  it("uses the scoped review loader and hides owner export actions", async () => {
    api.artifact.mockClear();
    const previewLoader = vi
      .fn()
      .mockResolvedValue({ content: { text: "Pinned review v1" }, files: [] });
    const wrapper = mount(WorkflowHumanTodo, {
      props: {
        todo,
        busy: false,
        paused: false,
        cancelled: false,
        restricted: true,
        previewLoader,
      },
      global: { stubs },
    });
    try {
      await wrapper.get('[data-testid="human-preview"]').trigger("click");
      await flushPromises();
      expect(previewLoader).toHaveBeenCalledWith("todo");
      expect(api.artifact).not.toHaveBeenCalled();
      expect(wrapper.text()).toContain("Pinned review v1");
      expect(wrapper.text()).not.toContain(" · task");
      await wrapper.setProps({ todo: { ...todo, status: "approved" } });
      expect(wrapper.find('[data-testid="human-export"]').exists()).toBe(false);
    } finally {
      wrapper.unmount();
    }
  });
  it.each(["text/html", "application/octet-stream"])(
    "previews untrusted %s files without opening an executable page",
    async (type) => {
      const content =
        "<script>window.previewInjected = true</script><h1>Complete report</h1>";
      api.artifact.mockResolvedValue({
        content: { text: "Report" },
        files: [{ file_id: "file", filename: "report.html" }],
      });
      api.file.mockResolvedValue({ data: new Blob([content], { type }) });
      const open = vi.spyOn(window, "open").mockReturnValue(null);
      const wrapper = mount(WorkflowHumanTodo, {
        props: { todo, busy: false, paused: false, cancelled: false },
        global: { stubs },
      });
      try {
        await wrapper.get('[data-testid="human-preview"]').trigger("click");
        await flushPromises();
        await wrapper
          .findAll("button")
          .find((button) => button.text().includes("report.html"))!
          .trigger("click");
        await flushPromises();
        expect(api.file).toHaveBeenLastCalledWith("report", 1, "file", false);
        expect(open).not.toHaveBeenCalled();
        if (type === "text/html") {
          expect(
            wrapper.get('[data-testid="human-file-content"]').text(),
          ).toContain(content);
          expect(wrapper.find("script").exists()).toBe(false);
        } else expect(wrapper.get("iframe").attributes("sandbox")).toBe("");
      } finally {
        wrapper.unmount();
        open.mockRestore();
      }
    },
  );
  it("previews every contracted field before approving a structured result", async () => {
    api.artifact.mockResolvedValue({
      artifact_id: "report",
      version: 1,
      content: {
        text: "Report summary",
        evidence: ["Complete evidence"],
        review_score: 42,
      },
      files: [],
    });
    const wrapper = mount(WorkflowHumanTodo, {
      props: { todo, busy: false, paused: false, cancelled: false },
      global: { stubs },
    });
    try {
      await wrapper.get('[data-testid="human-preview"]').trigger("click");
      await flushPromises();
      expect(wrapper.text()).toContain("Report summary");
      expect(wrapper.text()).toContain("Complete evidence");
      expect(wrapper.text()).toContain("review_score");
      expect(wrapper.text()).toContain("42");
    } finally {
      wrapper.unmount();
    }
  });
  it("previews the pinned version before approval and exports that version", async () => {
    api.artifact.mockResolvedValue({
      artifact_id: "report",
      version: 1,
      content: { text: "Report v1" },
      files: [],
    });
    api.export.mockResolvedValue({
      artifact_id: "report",
      version: 1,
      content: { text: "Report v1" },
      files: [],
    });
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});
    const wrapper = mount(WorkflowHumanTodo, {
      props: { todo, busy: false, paused: false, cancelled: false },
      global: { stubs },
    });
    try {
      expect(
        wrapper.get('[data-testid="human-submit"]').attributes("disabled"),
      ).toBeDefined();
      await wrapper.get('[data-testid="human-preview"]').trigger("click");
      await flushPromises();
      expect(api.artifact).toHaveBeenLastCalledWith("report", 1);
      expect(wrapper.text()).toContain("Report v1");
      await wrapper.get('[data-testid="human-submit"]').trigger("click");
      const emitted: any = wrapper.emitted("submit")?.[0];
      expect(emitted).toEqual([
        "todo",
        {
          request_id: expect.any(String),
          action: "approve",
          answer: null,
          feedback: null,
          artifact_ref: todo.artifact_ref,
        },
      ]);
      await wrapper.setProps({ todo: { ...todo, status: "approved" } });
      await wrapper.get('[data-testid="human-export"]').trigger("click");
      await flushPromises();
      expect(api.export).toHaveBeenLastCalledWith("report", 1);
      expect(click).toHaveBeenCalledTimes(1);
    } finally {
      wrapper.unmount();
      click.mockRestore();
    }
  });

  it("discards a stale preview when the request changes", async () => {
    let resolve!: (value: any) => void;
    api.artifact.mockReturnValue(
      new Promise((done) => {
        resolve = done;
      }),
    );
    const wrapper = mount(WorkflowHumanTodo, {
      props: { todo, busy: false, paused: false, cancelled: false },
      global: { stubs },
    });
    try {
      await wrapper.get('[data-testid="human-preview"]').trigger("click");
      await wrapper.setProps({
        todo: {
          ...todo,
          todo_id: "new",
          artifact_ref: { artifact_id: "report", version: 2 },
        },
      });
      resolve({ content: { text: "Old version" }, files: [] });
      await flushPromises();
      expect(wrapper.text()).not.toContain("Old version");
      expect(
        wrapper.get('[data-testid="human-submit"]').attributes("disabled"),
      ).toBeDefined();
    } finally {
      wrapper.unmount();
    }
  });

  it("requires rejection feedback and reuses request identity on repeated submit", async () => {
    const wrapper = mount(WorkflowHumanTodo, {
      props: { todo, busy: false, paused: true, cancelled: false },
      global: { stubs },
    });
    try {
      expect(
        wrapper.get('[data-testid="human-reject"]').attributes("disabled"),
      ).toBeDefined();
      api.artifact.mockResolvedValue({
        content: { text: "Report" },
        files: [],
      });
      await wrapper.get('[data-testid="human-preview"]').trigger("click");
      await flushPromises();
      await wrapper
        .get('[data-testid="human-feedback"]')
        .setValue("Missing evidence");
      await wrapper.get('[data-testid="human-reject"]').trigger("click");
      await wrapper.get('[data-testid="human-reject"]').trigger("click");
      expect(wrapper.emitted("submit")?.[0]).toEqual(
        wrapper.emitted("submit")?.[1],
      );
      expect(wrapper.emitted("submit")?.[0]?.[1]).toMatchObject({
        action: "reject",
        feedback: "Missing evidence",
      });
      await wrapper.setProps({ busy: true });
      expect(
        wrapper.get('[data-testid="human-reject"]').attributes("disabled"),
      ).toBeDefined();
    } finally {
      wrapper.unmount();
    }
  });

  it("saves a question answer while paused and shows its pending consumption", async () => {
    vi.stubGlobal("crypto", { randomUUID: undefined });
    const question = { ...todo, kind: "question", artifact_ref: null };
    const wrapper = mount(WorkflowHumanTodo, {
      props: { todo: question, busy: false, paused: true, cancelled: false },
      global: { stubs },
    });
    try {
      await wrapper.get('[data-testid="human-answer"]').setValue("Asia");
      await wrapper.get('[data-testid="human-submit"]').trigger("click");
      expect(wrapper.emitted("submit")?.[0]?.[1]).toMatchObject({
        action: "answer",
        answer: "Asia",
        request_id: expect.any(String),
      });
      await wrapper.setProps({
        todo: { ...question, status: "answered", response: { answer: "Asia" } },
      });
      expect(wrapper.text()).toContain("human.savedWaiting");
      expect(wrapper.find('[data-testid="human-submit"]').exists()).toBe(false);
    } finally {
      wrapper.unmount();
      vi.unstubAllGlobals();
    }
  });

  it("ignores a preview response after changing runs unmounts its request", async () => {
    let finish!: (value: any) => void;
    api.artifact.mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    const wrapper = mount(WorkflowHumanTodo, {
      props: { todo, busy: false, paused: false, cancelled: false },
      global: { stubs },
    });
    await wrapper.get('[data-testid="human-preview"]').trigger("click");
    wrapper.unmount();
    finish({ content: { text: "Old run content" }, files: [] });
    await flushPromises();
    expect(wrapper.emitted("submit")).toBeUndefined();
  });
});
