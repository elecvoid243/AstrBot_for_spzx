// 2026-07-26: covers the Ctrl/Cmd+S keyboard shortcut wired in
// DocumentEditor's onKeyDown handler. The keydown listener is
// attached on `document` in capture phase, so a real `keydown`
// event is dispatched on document.body and observed via the
// `save` emit on the wrapper. The CodeMirrorEditor child is
// stubbed to a textarea (matches the GitIgnoreEditor.spec.ts
// pattern) so the buffer / isDirty logic in DocumentEditor
// is exercised without booting the real editor.
import { flushPromises, mount } from "@vue/test-utils";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const tmMock = vi.fn(
  (key: string, params?: Record<string, string | number>) => {
    if (!params) return key;
    return Object.entries(params).reduce(
      (acc, [k, v]) => `${acc} ${k}=${String(v)}`,
      key,
    );
  },
);
vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({ tm: tmMock, getRaw: vi.fn() }),
}));

vi.mock("@/utils/clipboard", () => ({
  copyToClipboard: vi.fn().mockResolvedValue(undefined),
}));

vi.mock("./CodeMirrorEditor.vue", async () => {
  const { computed, defineComponent, ref, watch } = await import("vue");
  return {
    default: defineComponent({
      props: { modelValue: { type: String, default: "" } },
      // The real component emits both: update:modelValue on every
      // keystroke and dirty-change on every transition (clean<->dirty).
      // The stub mirrors both so the parent DocumentEditor's
      // isDirty wiring still flips during the test.
      emits: ["update:modelValue", "dirty-change"],
      setup(props, { emit }) {
        const buffer = ref(props.modelValue);
        const dirty = computed(() => buffer.value !== props.modelValue);
        watch(dirty, (d) => emit("dirty-change", d));
        function onInput(e: Event) {
          const v = (e.target as HTMLTextAreaElement).value;
          buffer.value = v;
          emit("update:modelValue", v);
        }
        return { buffer, onInput };
      },
      template: '<textarea :value="buffer" @input="onInput" />',
    }),
  };
});

import DocumentEditor from "./DocumentEditor.vue";

const vuetifyStubs = {
  "v-icon": { template: "<i />" },
};

function mountEditor(props: Record<string, unknown> = {}) {
  // No `attachTo: document.body` — the keydown listener is
  // registered on `document` in capture phase, so dispatching
  // directly on the `document` object (no body needed) is enough
  // to hit the same path the user pressing the key would.
  return mount(DocumentEditor, {
    props: {
      initialContent: "",
      fileRelative: "doc.md",
      isSaving: false,
      isDeleting: false,
      isRenaming: false,
      renameErrorMessage: null,
      // 2026-07-26 save-success-toast: DocumentEditor now also
      // takes saveSuccessMessage for the post-save inline
      // confirmation; default null = "no toast" so existing
      // tests do not need to be updated.
      saveSuccessMessage: null,
      ...props,
    },
    global: { stubs: vuetifyStubs },
  });
}

async function setBufferDirty(
  w: ReturnType<typeof mountEditor>,
): Promise<void> {
  // Find the stubbed editor's textarea and type a character to
  // flip the parent DocumentEditor.isDirty to true.
  const ta = w.find("textarea");
  await ta.setValue("x");
  await w.vm.$nextTick();
}

function dispatchKeydown(options: {
  key: string;
  ctrlKey?: boolean;
  metaKey?: boolean;
}) {
  // The component's onKeyDown listener is attached on `document`
  // in capture phase. Dispatching a bubbling, cancelable keydown
  // on `document` directly hits the same code path the user's
  // physical key would.
  const ev = new KeyboardEvent("keydown", {
    key: options.key,
    ctrlKey: options.ctrlKey ?? false,
    metaKey: options.metaKey ?? false,
    bubbles: true,
    cancelable: true,
  });
  document.dispatchEvent(ev);
  return ev;
}

describe("DocumentEditor", () => {
  let wrapper: ReturnType<typeof mountEditor> | null = null;
  beforeEach(() => {
    tmMock.mockClear();
  });
  afterEach(() => {
    wrapper?.unmount();
    wrapper = null;
  });

  it("stops dirty file management when save fails and preserves its draft", async () => {
    const save = vi.fn().mockResolvedValue(false);
    wrapper = mountEditor({
      initialContent: "old",
      fileAbsolute: "C:/A/doc.md",
      saveForAction: save,
    });
    await setBufferDirty(wrapper);
    await wrapper
      .findAll("button")
      .find((button) => button.text().endsWith("editor.delete"))!
      .trigger("click");
    await flushPromises();
    await wrapper
      .findAll("v-btn")
      .find((button) => button.text().endsWith("editor.leaveSave"))!
      .trigger("click");
    await flushPromises();
    expect(save).toHaveBeenCalledWith("x");
    expect(wrapper.emitted("delete")).toBeUndefined();
    expect(wrapper.find("textarea").element.value).toBe("x");
  });

  it("invalidates an open delete confirmation when the actual target changes", async () => {
    wrapper = mountEditor({
      initialContent: "old",
      fileAbsolute: "C:/A/doc.md",
    });
    await wrapper
      .findAll("button")
      .find((button) => button.text().endsWith("editor.delete"))!
      .trigger("click");
    await flushPromises();
    expect(wrapper.find(".document-editor__confirm").text()).toContain(
      "C:/A/doc.md",
    );
    await wrapper.setProps({ fileAbsolute: "C:/B/doc.md" });
    expect(wrapper.find(".document-editor__confirm").exists()).toBe(false);
    expect(wrapper.emitted("delete")).toBeUndefined();
  });

  it("does not save a different draft while a delete confirmation is pending", async () => {
    wrapper = mountEditor({ initialContent: "old", fileAbsolute: "C:/A/doc.md" });
    await wrapper.findAll("button").find((button) => button.text().endsWith("editor.delete"))!.trigger("click");
    await flushPromises();
    await setBufferDirty(wrapper);
    dispatchKeydown({ key: "s", ctrlKey: true });
    await flushPromises();
    expect(wrapper.emitted("save")).toBeUndefined();
  });

  it("Ctrl+S emits save with the current buffer when dirty", async () => {
    wrapper = mountEditor({ initialContent: "a" });
    await setBufferDirty(wrapper);
    const ev = dispatchKeydown({ key: "s", ctrlKey: true });
    await flushPromises();
    expect(ev.defaultPrevented).toBe(true);
    expect(wrapper.emitted("save")).toEqual([["x"]]);
  });

  it("Cmd+S (mac) also emits save when dirty", async () => {
    wrapper = mountEditor({ initialContent: "a" });
    await setBufferDirty(wrapper);
    dispatchKeydown({ key: "s", metaKey: true });
    await flushPromises();
    expect(wrapper.emitted("save")).toEqual([["x"]]);
  });

  it("uppercase 'S' (shift+ctrl+s) also triggers save", async () => {
    wrapper = mountEditor({ initialContent: "a" });
    await setBufferDirty(wrapper);
    dispatchKeydown({ key: "S", ctrlKey: true });
    await flushPromises();
    expect(wrapper.emitted("save")).toEqual([["x"]]);
  });

  it("Ctrl+S is a no-op when buffer is clean", async () => {
    wrapper = mountEditor({ initialContent: "a" });
    dispatchKeydown({ key: "s", ctrlKey: true });
    await flushPromises();
    expect(wrapper.emitted("save")).toBeFalsy();
  });

  it("Ctrl+S is a no-op while a save is in flight", async () => {
    wrapper = mountEditor({ initialContent: "a", isSaving: true });
    await setBufferDirty(wrapper);
    // isSaving is true: the dirty check would normally fire, but
    // the isSaving branch in onSave() gates the emit. The
    // dispatch still preventDefaults, but no save is emitted.
    dispatchKeydown({ key: "s", ctrlKey: true });
    await flushPromises();
    expect(wrapper.emitted("save")).toBeFalsy();
  });

  it("plain 's' (no modifier) does not save and does not preventDefault", async () => {
    wrapper = mountEditor({ initialContent: "a" });
    await setBufferDirty(wrapper);
    const ev = dispatchKeydown({ key: "s" });
    await flushPromises();
    expect(ev.defaultPrevented).toBe(false);
    expect(wrapper.emitted("save")).toBeFalsy();
  });

  it("save button has the saveHint tooltip", () => {
    wrapper = mountEditor();
    // The primary save button is the first .editor-bar__btn--primary
    // in the toolbar; its :title pulls the new saveHint i18n key.
    const btn = wrapper.find(".editor-bar__btn--primary");
    expect(btn.attributes("title")).toBe(
      "spcodeProjectLoad.documentManager.editor.saveHint",
    );
  });

  it("workspace notice does not reserve the toolbar's leading space", () => {
    const source = readFileSync(
      resolve(
        "src/components/chat/message_list_comps/FileBrowserFilePreview.vue",
      ),
      "utf8",
    );
    const noticeRule =
      source.match(/\.preview-editor-notice\s*\{([^}]*)\}/)?.[1] ?? "";

    expect(noticeRule).toMatch(/flex:\s*0\s+1\s+auto\s*;/);
    expect(noticeRule).not.toMatch(/flex:\s*1\s*;/);
  });

  it("shared editor bar explicitly left-aligns its leading controls", () => {
    const css = readFileSync(resolve("src/styles/editor-bar.css"), "utf8");
    const editorBarRule = css.match(/\.editor-bar\s*\{([^}]*)\}/)?.[1] ?? "";

    expect(editorBarRule).toMatch(/justify-content:\s*flex-start\s*;/);
  });
});
