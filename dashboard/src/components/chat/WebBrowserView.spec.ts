// Author: elecvoid243 @ 2026-10-22
// Behavioural coverage for the Web preview tab: address submission,
// bookmarks and project-scoped restore.

import { beforeEach, describe, expect, it } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import WebBrowserView from "./WebBrowserView.vue";
import { loadBrowserState } from "./webBrowser";

const ROOT = "F:/github/webproj";
const OTHER_ROOT = "F:/github/otherproj";

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
