// Author: elecvoid243, 2026-07-25 (redesigned 2026-07-31)
import { mount, type VueWrapper } from "@vue/test-utils";
import { defineComponent, nextTick, ref, type PropType } from "vue";
import { describe, expect, it, vi, beforeEach } from "vitest";

// ── Mocks ──────────────────────────────────────────────────────────────
// The redesigned dialog reads the loaded-project chip (to decide whether
// to prompt for overwrite) and the injected confirm dialog directly, so
// both are mocked here. The hoisted factory must NOT use `ref` (importing
// `vue` at module top level while also mocking a composable creates an
// init-cycle), so it only builds plain data + a plain vi.fn; the reactive
// ref is created below, after the vue import has finished initializing.
const { plainStatus, mockConfirm } = vi.hoisted(() => ({
  plainStatus: {
    loaded: false,
    directory: null,
    loadedAt: null,
    umo: null,
    allLoadedCount: 0,
    fetchedAt: 0,
  },
  mockConfirm: vi.fn(),
}));

vi.mock("@/composables/useSpcodeProjectStatus", () => ({
  useSpcodeProjectStatus: () => ({ status: mockStatus }),
}));

vi.mock("@/utils/confirmDialog", () => ({
  useConfirmDialog: () => mockConfirm,
}));

// Reactive view of the plain hoisted status. Re-assigning
// `mockStatus.value` in `beforeEach` flips `loaded` per test.
const mockStatus = ref(plainStatus);

import ProjectLoadDialog from "./ProjectLoadDialog.vue";

const dialogStub = defineComponent({
  props: {
    modelValue: { type: Boolean, default: false },
  },
  template: '<div v-if="modelValue"><slot /></div>',
});

const textFieldStub = defineComponent({
  props: {
    modelValue: { type: String, default: "" },
  },
  emits: ["update:modelValue"],
  template: `
    <div>
      <input
        data-testid="project-path"
        :value="modelValue"
        @input="$emit('update:modelValue', $event.target.value)"
      />
      <slot name="append-inner" />
    </div>
  `,
});

const checkboxStub = defineComponent({
  props: {
    modelValue: { type: Boolean, default: false },
    label: { type: String, default: "" },
  },
  emits: ["update:modelValue"],
  template: `
    <label class="v-label">
      <input
        type="checkbox"
        :checked="modelValue"
        @change="$emit('update:modelValue', $event.target.checked)"
      />
      <span>{{ label }}</span>
    </label>
  `,
});

// `buttonStub` mirrors any `value` fallthrough attr onto a `data-value`
// attribute so group stubs (e.g. the radio group above) can identify a
// clicked option from the native event bubbling up to them.
const buttonStub = defineComponent({
  props: {
    disabled: { type: Boolean, default: false },
  },
  emits: ["click"],
  template: `
    <button :disabled="disabled" :data-value="$attrs.value" @click="$emit('click')">
      <slot />
    </button>
  `,
});

// `radioStub` mirrors the `value` prop onto a `data-value` attribute so
// the `v-radio-group` stub below can read which option was clicked from
// the native click event that bubbles up to it. It renders as a native
// button so the tests' `clickOption` helper can find it by label text.
const radioStub = defineComponent({
  props: {
    value: { type: String, default: "" },
    label: { type: String, default: "" },
  },
  template: `<button type="button" :data-value="value">{{ label }}</button>`,
});

// Lightweight `v-radio-group` stub: it renders its `v-radio` slot and,
// on a bubbling native click, re-emits the clicked radio's `data-value`
// as the new model value. This lets tests drive the mode / kind radio
// groups without a full Vuetify group binding.
const radioGroupStub = defineComponent({
  props: {
    modelValue: { type: String as PropType<string | null>, default: null },
  },
  emits: ["update:modelValue"],
  template: `<div class="v-radio-group" @click="onClick"><slot /></div>`,
  methods: {
    onClick(e: Event) {
      const target = e.target as HTMLElement | null;
      const button = target?.closest?.("button") as HTMLElement | null;
      const value = button?.dataset?.value;
      if (value !== undefined && value !== "") {
        this.$emit("update:modelValue", value);
      }
    },
  },
});

// Stub for ProjectDirectoryBrowser: reflects browserOpen via modelValue and
// emits a fixed picked path when its test button is clicked.
const directoryBrowserStub = defineComponent({
  name: "ProjectDirectoryBrowser",
  props: { modelValue: { type: Boolean, default: false } },
  emits: ["update:modelValue", "select"],
  template: `<button data-testid="dir-browser-emit" @click="$emit('select','C:/picked/dir')" />`,
});

const stubs = {
  "v-dialog": dialogStub,
  "v-card": { template: "<div><slot /></div>" },
  "v-card-title": { template: "<div><slot /></div>" },
  "v-card-text": { template: "<div><slot /></div>" },
  "v-card-actions": { template: "<div><slot /></div>" },
  "v-form": { template: "<form><slot /></form>" },
  "v-text-field": textFieldStub,
  "v-checkbox": checkboxStub,
  "v-btn": buttonStub,
  "v-radio": radioStub,
  "v-radio-group": radioGroupStub,
  "v-divider": { template: "<hr />" },
  "v-spacer": { template: "<span />" },
  "v-list": { template: "<div><slot /></div>" },
  "v-list-item": { template: "<div><slot /></div>" },
  "v-list-item-title": { template: "<div><slot /></div>" },
  // 2026-08-15: the in-app directory browser. Stub records the open state
  // and can emit a picked backend path on demand.
  ProjectDirectoryBrowser: directoryBrowserStub,
};

function mountDialog(commandMode: "project" | "codegraph" = "project") {
  return mount(ProjectLoadDialog, {
    props: {
      wakePrefixes: ["/"],
      commandMode,
    },
    global: { stubs },
  });
}

async function openDialog(wrapper: VueWrapper): Promise<void> {
  (
    wrapper.vm as unknown as {
      openLoadDialog: () => void;
    }
  ).openLoadDialog();
  await nextTick();
}

function buttonByText(wrapper: VueWrapper, text: string, last = false) {
  const found = wrapper
    .findAll("button")
    .filter((button) => button.text().trim() === text);
  expect(found.length, `button with text "${text}" not found`).toBeGreaterThan(
    0,
  );
  return last ? found[found.length - 1] : found[0];
}

async function clickOption(wrapper: VueWrapper, text: string): Promise<void> {
  await buttonByText(wrapper, text).trigger("click");
  await nextTick();
}

async function submitPayload(
  wrapper: VueWrapper,
  path: string,
  buttonText = "加载",
): Promise<Record<string, unknown>> {
  await wrapper.get('[data-testid="project-path"]').setValue(path);
  // last=true: a codegraph sub-page tab and its submit button can share a
  // label ("设为默认目录"), and the submit button renders after the tabs.
  await buttonByText(wrapper, buttonText, true).trigger("click");
  await nextTick();
  return wrapper.emitted("submit")!.at(-1)![0] as Record<string, unknown>;
}

function checkboxStates(wrapper: VueWrapper): boolean[] {
  return wrapper
    .findAll<HTMLInputElement>('input[type="checkbox"]')
    .map((checkbox) => checkbox.element.checked);
}

beforeEach(() => {
  mockStatus.value = {
    loaded: false,
    directory: null,
    loadedAt: null,
    umo: null,
    allLoadedCount: 0,
    fetchedAt: 0,
  };
  mockConfirm.mockReset();
});

describe("ProjectLoadDialog load-step options", () => {
  it("shows both project load steps selected by default (existing + code)", async () => {
    const wrapper = mountDialog();
    await openDialog(wrapper);

    expect(checkboxStates(wrapper)).toEqual([true, true]);
    expect(wrapper.text()).toContain("加载 AGENTS.md");
    expect(wrapper.text()).toContain("加载 Codegraph");
  });

  it("renders the mode and kind radio groups with always-visible options", async () => {
    const wrapper = mountDialog();
    await openDialog(wrapper);

    const text = wrapper.text();
    expect(text).toContain("加载已有项目");
    expect(text).toContain("新建一个项目");
    expect(text).toContain("代码项目");
    expect(text).toContain("普通项目");
    // The old "Advanced settings" expansion panel is gone: the two
    // checkboxes are visible without expanding anything.
    expect(text).not.toContain("高级设置");
  });

  it("switching to create hides AGENTS.md/Codegraph and shows auto-init-git on", async () => {
    const wrapper = mountDialog();
    await openDialog(wrapper);

    await clickOption(wrapper, "新建一个项目");

    expect(checkboxStates(wrapper)).toEqual([true]);
    expect(wrapper.text()).toContain("自动初始化 Git 仓库");
    expect(wrapper.text()).not.toContain("加载 AGENTS.md");
    expect(wrapper.text()).not.toContain("加载 Codegraph");
  });

  it("switching kind to plain unchecks both, switching back to code rechecks", async () => {
    const wrapper = mountDialog();
    await openDialog(wrapper);

    await clickOption(wrapper, "普通项目");
    expect(checkboxStates(wrapper)).toEqual([false, false]);

    await clickOption(wrapper, "代码项目");
    expect(checkboxStates(wrapper)).toEqual([true, true]);
  });

  it.each([
    [true, true, "/project load C:/projects/demo", false, false],
    [false, true, "/project load C:/projects/demo no_agentsmd", true, false],
    [true, false, "/project load C:/projects/demo no_codegraph", false, true],
    [
      false,
      false,
      "/project load C:/projects/demo no_agentsmd no_codegraph",
      true,
      true,
    ],
  ])(
    "maps AGENTS.md=%s and Codegraph=%s to the expected payload",
    async (loadAgentsMd, loadCodegraph, expectedText, noAgentsmd, noCodegraph) => {
      const wrapper = mountDialog();
      await openDialog(wrapper);
      const checkboxes = wrapper.findAll<HTMLInputElement>(
        'input[type="checkbox"]',
      );

      if (!loadAgentsMd) await checkboxes[0].setValue(false);
      if (!loadCodegraph) await checkboxes[1].setValue(false);

      const payload = await submitPayload(wrapper, "C:/projects/demo");
      expect(payload).toMatchObject({
        mode: "project",
        path: "C:/projects/demo",
        noAgentsmd,
        noCodegraph,
        create: false,
        gitInit: false,
        force: false,
        legacyText: expectedText,
      });
    },
  );

  it("quotes a whitespace path in legacyText before appending flags", async () => {
    const wrapper = mountDialog();
    await openDialog(wrapper);
    const checkboxes = wrapper.findAll<HTMLInputElement>(
      'input[type="checkbox"]',
    );
    await checkboxes[1].setValue(false);

    const payload = await submitPayload(wrapper, "C:/projects/my app");
    expect(payload.legacyText).toBe(
      '/project load "C:/projects/my app" no_codegraph',
    );
    // Structured path is the raw (unquoted) user input.
    expect(payload.path).toBe("C:/projects/my app");
    expect(payload.noCodegraph).toBe(true);
  });

  it("create mode submits with create + git_init and skips AGENTS.md/Codegraph", async () => {
    const wrapper = mountDialog();
    await openDialog(wrapper);
    await clickOption(wrapper, "新建一个项目");

    const payload = await submitPayload(wrapper, "C:/projects/new");
    expect(payload.legacyText).toBe(
      "/project load C:/projects/new no_agentsmd no_codegraph create git_init",
    );
    expect(payload).toMatchObject({
      create: true,
      gitInit: true,
      noAgentsmd: true,
      noCodegraph: true,
    });
  });

  it("create mode without auto-init-git omits git_init", async () => {
    const wrapper = mountDialog();
    await openDialog(wrapper);
    await clickOption(wrapper, "新建一个项目");
    await wrapper
      .findAll<HTMLInputElement>('input[type="checkbox"]')[0]
      .setValue(false);

    const payload = await submitPayload(wrapper, "C:/projects/new");
    expect(payload.legacyText).toBe(
      "/project load C:/projects/new no_agentsmd no_codegraph create",
    );
    expect(payload).toMatchObject({ create: true, gitInit: false });
  });

  it("existing plain project submits without create/git_init", async () => {
    const wrapper = mountDialog();
    await openDialog(wrapper);
    await clickOption(wrapper, "普通项目");

    const payload = await submitPayload(wrapper, "C:/projects/notes");
    expect(payload.legacyText).toBe(
      "/project load C:/projects/notes no_agentsmd no_codegraph",
    );
    expect(payload).toMatchObject({ create: false, gitInit: false });
  });

  it("sets force and appends replace when a project is loaded and overwrite confirmed", async () => {
    mockStatus.value.loaded = true;
    mockConfirm.mockResolvedValueOnce(true);
    const wrapper = mountDialog();
    await openDialog(wrapper);

    const payload = await submitPayload(wrapper, "C:/projects/other");

    expect(payload.legacyText).toBe("/project load C:/projects/other replace");
    expect(payload.force).toBe(true);
    expect(mockConfirm).toHaveBeenCalledTimes(1);
  });

  it("does not submit when the overwrite prompt is cancelled", async () => {
    mockStatus.value.loaded = true;
    mockConfirm.mockResolvedValueOnce(false);
    const wrapper = mountDialog();
    await openDialog(wrapper);
    await wrapper.get('[data-testid="project-path"]').setValue("C:/x");

    await buttonByText(wrapper, "加载").trigger("click");
    await nextTick();

    expect(wrapper.emitted("submit")).toBeUndefined();
    expect(mockConfirm).toHaveBeenCalledTimes(1);
  });

  it("restores every option to its default each time the dialog opens", async () => {
    const wrapper = mountDialog();
    await openDialog(wrapper);
    await clickOption(wrapper, "新建一个项目");
    await clickOption(wrapper, "加载已有项目");
    await clickOption(wrapper, "普通项目");

    (
      wrapper.vm as unknown as {
        closeLoadDialog: () => void;
      }
    ).closeLoadDialog();
    await nextTick();
    await openDialog(wrapper);

    expect(checkboxStates(wrapper)).toEqual([true, true]);
    expect(wrapper.text()).not.toContain("自动初始化 Git 仓库");
  });

  it("initializes codegraph by default and keeps project load flags out", async () => {
    const wrapper = mountDialog("codegraph");
    await openDialog(wrapper);

    expect(wrapper.findAll('input[type="checkbox"]')).toHaveLength(0);
    const payload = await submitPayload(
      wrapper,
      "C:/projects/demo",
      "初始化 / 更新",
    );
    expect(payload).toMatchObject({
      mode: "codegraph-init",
      path: "C:/projects/demo",
      legacyText: "/codegraph init C:/projects/demo",
    });
  });

  it("sets the default codegraph directory from the second sub-page", async () => {
    const wrapper = mountDialog("codegraph");
    await openDialog(wrapper);

    await clickOption(wrapper, "设为默认目录");
    const payload = await submitPayload(
      wrapper,
      "C:/projects/demo",
      "设为默认目录",
    );
    expect(payload).toMatchObject({
      mode: "codegraph",
      path: "C:/projects/demo",
      legacyText: "/codegraph set C:/projects/demo",
    });
  });
});

describe("ProjectLoadDialog in-app file browser (2026-08-15)", () => {
  it("browse button opens the browser and a picked path fills the field", async () => {
    const wrapper = mountDialog();
    await openDialog(wrapper);

    const browseBtn = wrapper.find('[data-testid="browse-directory"]');
    expect(browseBtn.exists()).toBe(true);
    await browseBtn.trigger("click");

    const browser = wrapper.findComponent({ name: "ProjectDirectoryBrowser" });
    expect(browser.props("modelValue")).toBe(true);

    await wrapper.find('[data-testid="dir-browser-emit"]').trigger("click");
    const input = wrapper.get('[data-testid="project-path"]')
      .element as HTMLInputElement;
    expect(input.value).toBe("C:/picked/dir");
  });

  it("submits the browser-picked path through the normal load flow", async () => {
    const wrapper = mountDialog();
    await openDialog(wrapper);

    await wrapper.find('[data-testid="browse-directory"]').trigger("click");
    await wrapper.find('[data-testid="dir-browser-emit"]').trigger("click");

    await buttonByText(wrapper, "加载").trigger("click");
    await nextTick();

    const payload = wrapper.emitted("submit")!.at(-1)![0] as Record<
      string,
      unknown
    >;
    expect(payload.path).toBe("C:/picked/dir");
    expect(payload.legacyText).toBe("/project load C:/picked/dir");
  });
});
