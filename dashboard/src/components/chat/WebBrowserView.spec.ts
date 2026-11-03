// Author: elecvoid243 @ 2026-10-22
// Behavioural coverage for the Web preview tab: address submission,
// bookmarks and project-scoped restore.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { enableAutoUnmount, flushPromises, mount } from "@vue/test-utils";
import WebBrowserView from "./WebBrowserView.vue";
import { loadBrowserState } from "./webBrowser";

const ROOT = "F:/github/webproj";
const OTHER_ROOT = "F:/github/otherproj";
enableAutoUnmount(afterEach);

function mountView(projectRoot: string | null = ROOT) {
  return mount(WebBrowserView, { props: { projectRoot } });
}

async function submitAddress(
  wrapper: ReturnType<typeof mountView>,
  value: string,
) {
  const input = wrapper.get("input.web-browser-input");
  await input.setValue(value);
  await input.trigger("keydown.enter");
  await flushPromises();
}

function crossOriginUrl(): string {
  const url = new URL(window.location.href);
  const port = Number(url.port || (url.protocol === "https:" ? 443 : 80));
  url.port = String(port + 1);
  return `${url.origin}/`;
}

beforeEach(() => {
  localStorage.clear();
});

describe("WebBrowserView", () => {
  it("fits desktop pages to the available width without reloading tabs on resize", async () => {
    let measure!: ResizeObserverCallback;
    const disconnect = vi.fn();
    vi.stubGlobal(
      "ResizeObserver",
      class {
        constructor(callback: ResizeObserverCallback) {
          measure = callback;
        }
        observe() {}
        disconnect = disconnect;
      },
    );
    const wrapper = mountView();
    try {
      await submitAddress(wrapper, "localhost:3000");
      const frame = wrapper.get("iframe").element;
      for (const [width, height] of [
        [640, 480],
        [960, 540],
        [1600, 800],
      ]) {
        measure(
          [{ contentRect: { width, height } } as ResizeObserverEntry],
          {} as ResizeObserver,
        );
        await flushPromises();
        expect(wrapper.get("iframe").element).toBe(frame);
        expect(frame.style.width).toBe(`${Math.max(1280, width)}px`);
        const scale = width / Math.max(1280, width);
        expect(frame.style.transform).toBe(`scale(${scale})`);
        expect(Number.parseFloat(frame.style.height) * scale).toBeCloseTo(
          height,
        );
      }
      measure(
        [{ contentRect: { width: 0, height: 0 } } as ResizeObserverEntry],
        {} as ResizeObserver,
      );
      await flushPromises();
      expect(frame.style.width).toBe("1600px");
      await wrapper.get(".web-browser-add-tab").trigger("click");
      await submitAddress(wrapper, "localhost:5173");
      await wrapper.findAll(".web-browser-tab-select")[0].trigger("click");
      expect(wrapper.findAll("iframe")[0].element).toBe(frame);
      wrapper.unmount();
      expect(disconnect).toHaveBeenCalledOnce();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("keeps the iframe in place through native fullscreen, Escape and hiding the panel", async () => {
    let owner: Element | null = null;
    const elementDescriptor = Object.getOwnPropertyDescriptor(
      document,
      "fullscreenElement",
    );
    const exitDescriptor = Object.getOwnPropertyDescriptor(
      document,
      "exitFullscreen",
    );
    Object.defineProperty(document, "fullscreenElement", {
      configurable: true,
      get: () => owner,
    });
    const exit = vi.fn(async () => {
      owner = null;
      document.dispatchEvent(new Event("fullscreenchange"));
    });
    Object.defineProperty(document, "exitFullscreen", {
      configurable: true,
      value: exit,
    });
    const wrapper = mountView();
    try {
      const request = vi.fn(async () => {
        owner = wrapper.element;
        document.dispatchEvent(new Event("fullscreenchange"));
      });
      Object.defineProperty(wrapper.element, "requestFullscreen", {
        value: request,
      });
      await submitAddress(wrapper, "localhost:3000");
      const frame = wrapper.get("iframe").element;
      const button = wrapper.get(".web-browser-fullscreen");
      await button.trigger("click");
      await flushPromises();
      expect(request).toHaveBeenCalledOnce();
      expect(button.attributes("aria-pressed")).toBe("true");
      expect(button.attributes("aria-label")).toContain("退出全屏");
      expect(wrapper.get("iframe").element).toBe(frame);
      // Native Escape exits via fullscreenchange, even with focus in a cross-origin frame.
      await exit();
      await flushPromises();
      expect(button.attributes("aria-pressed")).toBe("false");
      await button.trigger("click");
      await button.trigger("click");
      await flushPromises();
      expect(owner).toBeNull();
      await button.trigger("click");
      await wrapper.setProps({ visible: false });
      await flushPromises();
      expect(owner).toBeNull();
      expect(wrapper.get("iframe").element).toBe(frame);
    } finally {
      wrapper.unmount();
      if (elementDescriptor)
        Object.defineProperty(document, "fullscreenElement", elementDescriptor);
      else delete (document as any).fullscreenElement;
      if (exitDescriptor)
        Object.defineProperty(document, "exitFullscreen", exitDescriptor);
      else delete (document as any).exitFullscreen;
    }
  });

  it("reports denied fullscreen without replacing the current page", async () => {
    const wrapper = mountView();
    await submitAddress(wrapper, "localhost:3000");
    const frame = wrapper.get("iframe").element;
    Object.defineProperty(wrapper.element, "requestFullscreen", {
      value: vi.fn().mockRejectedValue(new Error("Denied")),
    });
    await wrapper.get(".web-browser-fullscreen").trigger("click");
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toContain("全屏权限");
    expect(wrapper.get("iframe").element).toBe(frame);
    expect(
      wrapper.get(".web-browser-fullscreen").attributes("aria-pressed"),
    ).toBe("false");
  });

  it("starts on the empty state without an iframe", () => {
    const wrapper = mountView();
    expect(wrapper.find("iframe.web-browser-frame").exists()).toBe(false);
    expect(wrapper.get(".web-browser-empty").text()).toContain(
      "localhost:3000",
    );
  });

  it("loads the normalized address on Enter", async () => {
    const wrapper = mountView();
    await submitAddress(wrapper, "localhost:3000");
    expect(wrapper.get("iframe.web-browser-frame").attributes("src")).toBe(
      "http://localhost:3000/",
    );
    expect(wrapper.find(".web-browser-empty").exists()).toBe(false);
  });

  it("shows an inline error and keeps the iframe absent for bad schemes", async () => {
    const wrapper = mountView();
    await submitAddress(wrapper, "javascript:alert(1)");
    expect(wrapper.find("iframe.web-browser-frame").exists()).toBe(false);
    expect(wrapper.get(".web-browser-error").text()).toContain("http");
  });

  it("restores the address for the same project only", async () => {
    const first = mountView(ROOT);
    await submitAddress(first, "localhost:3000");
    expect(loadBrowserState(ROOT).url).toBe("http://localhost:3000/");
    first.unmount();

    const restored = mountView(ROOT);
    await flushPromises();
    expect(restored.get("iframe.web-browser-frame").attributes("src")).toBe(
      "http://localhost:3000/",
    );
    restored.unmount();

    const other = mountView(OTHER_ROOT);
    expect(other.find("iframe.web-browser-frame").exists()).toBe(false);
  });

  it("keeps pages mounted while switching tabs and closes only the selected page", async () => {
    const wrapper = mountView();
    await submitAddress(wrapper, "localhost:3000");
    const firstFrame = wrapper.get("iframe").element;
    await wrapper.get(".web-browser-add-tab").trigger("click");
    await submitAddress(wrapper, "localhost:5173");
    expect(wrapper.findAll("iframe")).toHaveLength(2);
    expect(wrapper.findAll("iframe")[0].element).toBe(firstFrame);

    await wrapper.findAll(".web-browser-tab-select")[0].trigger("click");
    expect((wrapper.get("input").element as HTMLInputElement).value).toBe(
      "http://localhost:3000/",
    );
    expect(
      wrapper.findAll(".web-browser-stage")[0].attributes("style") || "",
    ).not.toContain("display: none");
    expect(
      wrapper.findAll(".web-browser-stage")[1].attributes("style"),
    ).toContain("display: none");
    await wrapper.findAll(".web-browser-tab-close")[0].trigger("click");
    expect(wrapper.findAll("iframe")).toHaveLength(1);
    expect(wrapper.get("iframe").attributes("src")).toBe(
      "http://localhost:5173/",
    );
    await wrapper.get(".web-browser-tab-close").trigger("click");
    expect(wrapper.findAll(".web-browser-tab")).toHaveLength(1);
    expect(wrapper.find("iframe").exists()).toBe(false);
  });

  it("restores tabs and the active page independently for each project", async () => {
    const wrapper = mountView();
    await submitAddress(wrapper, "localhost:3000");
    await wrapper.get(".web-browser-add-tab").trigger("click");
    await submitAddress(wrapper, "localhost:5173");
    await wrapper.findAll(".web-browser-tab-select")[0].trigger("click");
    await flushPromises();
    await wrapper.setProps({ projectRoot: OTHER_ROOT });
    expect(wrapper.find("iframe").exists()).toBe(false);
    await submitAddress(wrapper, "localhost:8000");
    await wrapper.setProps({ projectRoot: ROOT });
    await flushPromises();
    expect(wrapper.findAll("iframe")).toHaveLength(2);
    expect((wrapper.get("input").element as HTMLInputElement).value).toBe(
      "http://localhost:3000/",
    );
    expect(loadBrowserState(OTHER_ROOT).url).toBe("http://localhost:8000/");
  });

  it("preserves drafts and reloads only the active page", async () => {
    const wrapper = mountView();
    await submitAddress(wrapper, "localhost:3000");
    await wrapper.get("input").setValue("unfinished address");
    await wrapper.get(".web-browser-add-tab").trigger("click");
    await submitAddress(wrapper, "localhost:5173");
    const [firstFrame, secondFrame] = wrapper
      .findAll("iframe")
      .map((frame) => frame.element);
    await wrapper
      .findAll(".web-browser-address .web-browser-icon-btn")[0]
      .trigger("click");
    expect(wrapper.findAll("iframe")[0].element).toBe(firstFrame);
    expect(wrapper.findAll("iframe")[1].element).not.toBe(secondFrame);
    await wrapper.findAll(".web-browser-tab-select")[0].trigger("click");
    expect((wrapper.get("input").element as HTMLInputElement).value).toBe(
      "unfinished address",
    );
    await wrapper.findAll(".web-browser-tab-close")[1].trigger("click");
    expect(wrapper.get("iframe").element).toBe(firstFrame);
  });

  it("adds and removes a bookmark for the current address", async () => {
    const wrapper = mountView();
    expect(
      wrapper.get(".web-browser-star").attributes("disabled"),
    ).toBeDefined();
    await submitAddress(wrapper, "localhost:3000");
    expect(wrapper.find(".web-browser-options").exists()).toBe(false);

    await wrapper.get(".web-browser-star").trigger("click");
    await flushPromises();
    expect(loadBrowserState(ROOT).bookmarks).toEqual([
      "http://localhost:3000/",
    ]);
    expect(wrapper.findAll(".web-browser-bookmark")).toHaveLength(1);

    await wrapper.get(".web-browser-bookmark-remove").trigger("click");
    await flushPromises();
    expect(loadBrowserState(ROOT).bookmarks).toEqual([]);
    expect(wrapper.findAll(".web-browser-bookmark")).toHaveLength(0);
  });

  it("updates the address-bar star for the active tab", async () => {
    const wrapper = mountView();
    await submitAddress(wrapper, "localhost:3000");
    await wrapper.get(".web-browser-add-tab").trigger("click");
    await submitAddress(wrapper, "localhost:5173");
    await wrapper.get(".web-browser-star").trigger("click");
    expect(loadBrowserState(ROOT).bookmarks).toEqual([
      "http://localhost:5173/",
    ]);
    expect(
      wrapper
        .get(".web-browser-address .web-browser-star")
        .attributes("aria-pressed"),
    ).toBe("true");
    await wrapper.findAll(".web-browser-tab-select")[0].trigger("click");
    expect(wrapper.get(".web-browser-star").attributes("aria-pressed")).toBe(
      "false",
    );
    await wrapper.findAll(".web-browser-tab-select")[1].trigger("click");
    expect(wrapper.get(".web-browser-star").attributes("aria-pressed")).toBe(
      "true",
    );
    await wrapper.get(".web-browser-star").trigger("click");
    expect(wrapper.find(".web-browser-options").exists()).toBe(false);
  });

  it("keeps allow-same-origin for cross-origin targets and drops it for dashboard routes", async () => {
    const wrapper = mountView();
    await submitAddress(wrapper, crossOriginUrl());
    expect(
      wrapper.get("iframe.web-browser-frame").attributes("sandbox"),
    ).toContain("allow-same-origin");

    await submitAddress(wrapper, `${window.location.origin}/plugin-view/demo`);
    expect(
      wrapper.get("iframe.web-browser-frame").attributes("sandbox"),
    ).not.toContain("allow-same-origin");
  });
});
