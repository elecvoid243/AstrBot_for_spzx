// ProjectLoadDialog spec — 2026-11-07 redesign.
//
// The project dialog is now path-only: loading never generates AGENTS.md,
// never initializes codegraph, and exposes no mode/kind/load-step
// controls. The codegraph management mode (init / set) is unchanged.
//
// Author: elecvoid243

import { mount, type VueWrapper } from "@vue/test-utils";
import { defineComponent, nextTick } from "vue";
import { describe, expect, it } from "vitest";

import ProjectLoadDialog from "./ProjectLoadDialog.vue";

const dialogStub = defineComponent({
  props: { modelValue: { type: Boolean, default: false } },
  template: '<div v-if="modelValue"><slot /></div>',
});

const textFieldStub = defineComponent({
  props: { modelValue: { type: String, default: "" } },
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

const buttonStub = defineComponent({
  props: { disabled: { type: Boolean, default: false } },
  emits: ["click"],
  template: `
    <button :disabled="disabled" @click="$emit('click')">
      <slot />
    </button>
  `,
});

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
  "v-btn": buttonStub,
  "v-spacer": { template: "<span />" },
  "v-list": { template: "<div><slot /></div>" },
  "v-list-item": { template: "<div><slot /></div>" },
  "v-list-item-title": { template: "<div><slot /></div>" },
  ProjectDirectoryBrowser: directoryBrowserStub,
};

function mountDialog(commandMode: "project" | "codegraph" = "project") {
  return mount(ProjectLoadDialog, {
    props: { wakePrefixes: ["/"], commandMode },
    global: { stubs },
  });
}

async function openDialog(wrapper: VueWrapper): Promise<void> {
  (
    wrapper.vm as unknown as { openLoadDialog: () => void }
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

async function submitPayload(
  wrapper: VueWrapper,
  path: string,
  buttonText = "加载",
): Promise<Record<string, unknown>> {
  await wrapper.get('[data-testid="project-path"]').setValue(path);
  await buttonByText(wrapper, buttonText, true).trigger("click");
  await nextTick();
  return wrapper.emitted("submit")!.at(-1)![0] as Record<string, unknown>;
}

describe("ProjectLoadDialog project mode", () => {
  it("renders only the path field", async () => {
    const wrapper = mountDialog();
    await openDialog(wrapper);

    const text = wrapper.text();
    expect(wrapper.find('[data-testid="project-path"]').exists()).toBe(true);
    expect(text).not.toContain("加载已有项目");
    expect(text).not.toContain("新建一个项目");
    expect(text).not.toContain("代码项目");
    expect(text).not.toContain("加载步骤");
    expect(text).not.toContain("加载 Codegraph");
    expect(wrapper.findAll('input[type="checkbox"]')).toHaveLength(0);
  });

  it("submits a plain path with no legacy flags", async () => {
    const wrapper = mountDialog();
    await openDialog(wrapper);

    const payload = await submitPayload(wrapper, "C:/projects/demo");
    expect(payload).toMatchObject({
      mode: "project",
      path: "C:/projects/demo",
      force: true,
      legacyText: "/project load C:/projects/demo replace",
    });
    expect(payload.noAgentsmd).toBeUndefined();
    expect(payload.noCodegraph).toBeUndefined();
    expect(payload.create).toBeUndefined();
    expect(payload.gitInit).toBeUndefined();
  });

  it("quotes a whitespace path", async () => {
    const wrapper = mountDialog();
    await openDialog(wrapper);

    const payload = await submitPayload(wrapper, "C:/projects/my app");
    expect(payload.legacyText).toBe(
      '/project load "C:/projects/my app" replace',
    );
    expect(payload.path).toBe("C:/projects/my app");
  });
});

describe("ProjectLoadDialog in-app file browser", () => {
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
    expect(payload.legacyText).toBe("/project load C:/picked/dir replace");
  });
});

describe("ProjectLoadDialog codegraph mode", () => {
  it("initializes codegraph by default", async () => {
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

    await buttonByText(wrapper, "设置默认目录").trigger("click");
    const payload = await submitPayload(
      wrapper,
      "C:/projects/demo",
      "设置默认目录",
    );
    expect(payload).toMatchObject({
      mode: "codegraph",
      path: "C:/projects/demo",
      legacyText: "/codegraph set C:/projects/demo",
    });
  });
});
