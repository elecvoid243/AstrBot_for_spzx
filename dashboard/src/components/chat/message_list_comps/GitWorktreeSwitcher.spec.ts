import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { enableAutoUnmount, mount } from "@vue/test-utils";
import { nextTick } from "vue";
import GitWorktreeSwitcher from "./GitWorktreeSwitcher.vue";
import type { SpcodeGitWorktree } from "@/composables/parseSpcodeWorktrees";

enableAutoUnmount(afterEach);

const worktrees: SpcodeGitWorktree[] = [
  {
    path: "/repo",
    branch: "main",
    headSha: "abcdef123",
    isMain: true,
    prunable: false,
    locked: null,
  },
  {
    path: "/repo-feature",
    branch: "feature",
    headSha: "123456789",
    isMain: false,
    prunable: false,
    locked: "Keep",
  },
  {
    path: "/repo-detached",
    branch: null,
    headSha: "987654321",
    isMain: false,
    prunable: true,
    locked: null,
  },
];
let stripHeight = 28;
let stripWidth = 300;
let resize: () => void;
const disconnect = vi.fn();
const mountOptions = {
  props: { worktrees, selectedPath: null },
  global: {
    stubs: {
      "v-menu": {
        props: ["modelValue"],
        emits: ["update:modelValue"],
        template: `<div>
          <slot name="activator" :props="{ onClick: () => $emit('update:modelValue', !modelValue) }" />
          <slot v-if="modelValue" />
        </div>`,
      },
      "v-list": { template: "<div><slot /></div>" },
      "v-list-item": {
        props: ["title", "subtitle", "active", "prependIcon"],
        template:
          '<button class="menu-item" :aria-selected="active"><i>{{ prependIcon }}</i>{{ title }}<span>{{ subtitle }}</span><slot name="append" /></button>',
      },
    },
  },
};

beforeEach(() => {
  stripHeight = 28;
  stripWidth = 300;
  disconnect.mockClear();
  vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockImplementation(
    function (this: HTMLElement) {
      return this.classList.contains("worktree-tabs") ? stripHeight : 28;
    },
  );
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockImplementation(
    () => stripWidth,
  );
  vi.stubGlobal("getComputedStyle", () => ({ rowGap: "6px" }));
  vi.stubGlobal(
    "ResizeObserver",
    class {
      constructor(callback: () => void) {
        resize = callback;
      }
      observe(element: HTMLElement) {
        element.style.rowGap = "6px";
      }
      disconnect = disconnect;
    },
  );
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("GitWorktreeSwitcher", () => {
  it("keeps up to two rows of buttons and restores them when space returns", async () => {
    const wrapper = mount(GitWorktreeSwitcher, mountOptions);
    await nextTick();
    resize();
    await nextTick();
    expect(wrapper.find(".worktree-menu-button").exists()).toBe(false);

    stripHeight = 62;
    resize();
    await nextTick();
    expect(wrapper.find(".worktree-menu-button").exists()).toBe(false);

    stripHeight = 96;
    resize();
    await nextTick();
    expect(wrapper.get(".worktree-menu-button").text()).toContain("main");
    expect(wrapper.get(".worktree-tabs").attributes("aria-hidden")).toBe(
      "true",
    );
    expect(wrapper.get(".worktree-tabs").attributes()).toHaveProperty("inert");
    expect(
      wrapper
        .findAll('[role="tab"]')
        .every((tab) => tab.attributes("tabindex") === "-1"),
    ).toBe(true);

    // Measuring the hidden full strip must keep dropdown mode stable.
    resize();
    await nextTick();
    await wrapper.get(".worktree-menu-button").trigger("click");
    expect(wrapper.findAll(".menu-item")).toHaveLength(3);

    stripHeight = 62;
    resize();
    await nextTick();
    expect(wrapper.find(".worktree-menu-button").exists()).toBe(false);
    expect(wrapper.get(".worktree-tabs").attributes()).not.toHaveProperty(
      "inert",
    );
    expect(wrapper.findAll(".menu-item")).toHaveLength(0);
  });

  it("remeasures a changed list and ignores a hidden sidebar's zero width", async () => {
    const wrapper = mount(GitWorktreeSwitcher, mountOptions);
    await nextTick();
    stripHeight = 96;
    resize();
    await nextTick();
    stripWidth = 0;
    stripHeight = 0;
    resize();
    await nextTick();
    expect(wrapper.find(".worktree-menu-button").exists()).toBe(true);

    await wrapper.setProps({ worktrees: worktrees.slice(0, 1) });
    stripWidth = 300;
    stripHeight = 28;
    resize();
    await nextTick();
    expect(wrapper.find(".worktree-menu-button").exists()).toBe(false);
    expect(wrapper.findAll('[role="tab"]')).toHaveLength(1);
  });

  it("preserves switching, creation and right-click management in both modes", async () => {
    const wrapper = mount(GitWorktreeSwitcher, mountOptions);
    await nextTick();
    await wrapper.findAll('[role="tab"]')[1].trigger("click");
    expect(wrapper.emitted("change")?.at(-1)).toEqual(["/repo-feature"]);
    await wrapper.findAll('[role="tab"]')[1].trigger("contextmenu");
    expect(wrapper.emitted("contextmenu")?.at(-1)?.[1]).toEqual(worktrees[1]);
    await wrapper.get(".worktree-add").trigger("click");
    expect(wrapper.emitted("create")).toHaveLength(1);

    await wrapper.setProps({ selectedPath: "/repo-feature" });
    stripHeight = 96;
    resize();
    await nextTick();
    expect(wrapper.get(".worktree-menu-button").text()).toContain("feature");
    await wrapper.get(".worktree-menu-button").trigger("click");
    const options = wrapper.findAll(".menu-item");
    expect(options[1].attributes("aria-selected")).toBe("true");
    expect(options[1].text()).toContain("mdi-lock");
    expect(options[2].text()).toContain("9876543");
    expect(options[2].text()).toContain("/repo-detached");
    await options[1].trigger("contextmenu");
    expect(wrapper.emitted("contextmenu")?.at(-1)?.[1]).toEqual(worktrees[1]);
    await options[0].trigger("click");
    expect(wrapper.emitted("change")?.at(-1)).toEqual([null]);
    await options[2].trigger("click");
    expect(wrapper.emitted("change")?.at(-1)).toEqual(["/repo-detached"]);
    await wrapper.get(".worktree-add").trigger("click");
    expect(wrapper.emitted("create")).toHaveLength(2);
  });

  it("disconnects layout observation on unmount", async () => {
    const wrapper = mount(GitWorktreeSwitcher, mountOptions);
    await nextTick();
    wrapper.unmount();
    expect(disconnect).toHaveBeenCalledOnce();
  });
});
