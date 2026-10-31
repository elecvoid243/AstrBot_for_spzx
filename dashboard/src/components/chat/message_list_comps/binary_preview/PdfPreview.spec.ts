import { describe, expect, it, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";

const getDocument = vi.hoisted(() => vi.fn());
vi.mock("pdfjs-dist", () => ({ GlobalWorkerOptions: {}, getDocument }));
import PdfPreview from "./PdfPreview.vue";

describe("PDF resource lifecycle", () => {
  it("destroys an in-flight document and never renders its late pages after switching", async () => {
    let finish!: (doc: unknown) => void;
    const oldTask = {
      promise: new Promise((resolve) => {
        finish = resolve;
      }),
      destroy: vi.fn(),
    };
    const oldDoc = { destroy: vi.fn(), numPages: 99, getPage: vi.fn() };
    const page = {
      getViewport: () => ({ width: 100, height: 100 }),
      render: vi.fn(() => ({ promise: Promise.resolve(), cancel: vi.fn() })),
      cleanup: vi.fn(),
    };
    const newDoc = {
      numPages: 1,
      getPage: vi.fn(async () => page),
      destroy: vi.fn(),
    };
    const newTask = { promise: Promise.resolve(newDoc), destroy: vi.fn() };
    getDocument.mockReturnValueOnce(oldTask).mockReturnValueOnce(newTask);
    Object.defineProperty(HTMLCanvasElement.prototype, "getContext", {
      configurable: true,
      value: vi.fn(() => ({})),
    });
    const first = new Blob(["old"]);
    first.arrayBuffer = async () => new ArrayBuffer(3);
    const second = new Blob(["new"]);
    second.arrayBuffer = async () => new ArrayBuffer(3);
    const wrapper = mount(PdfPreview, {
      props: { blob: first },
      global: {
        stubs: {
          "v-btn": { template: "<button><slot /></button>" },
          "v-icon": true,
        },
      },
    });
    await flushPromises();
    await wrapper.setProps({ blob: second });
    await flushPromises();
    expect(oldTask.destroy).toHaveBeenCalledOnce();
    finish(oldDoc);
    await flushPromises();
    expect(oldDoc.destroy).toHaveBeenCalledOnce();
    expect(oldDoc.getPage).not.toHaveBeenCalled();
    expect(newDoc.getPage).toHaveBeenCalledWith(1);
    expect(page.render).toHaveBeenCalledOnce();
    wrapper.unmount();
    expect(newTask.destroy).toHaveBeenCalledOnce();
    Reflect.deleteProperty(HTMLCanvasElement.prototype, "getContext");
  });
});
