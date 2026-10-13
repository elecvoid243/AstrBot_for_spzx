import { mount, type VueWrapper } from "@vue/test-utils";
import { nextTick } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ChatScrollMarkers, {
  type ChatScrollMarker,
} from "./ChatScrollMarkers.vue";

const wrappers: VueWrapper[] = [];
const frames = new Map<number, FrameRequestCallback>();
let frameId = 0;

function mountMarkers(count = 12, height = 600, coarse = false) {
  vi.spyOn(window, "matchMedia").mockReturnValue({
    matches: coarse,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  } as unknown as MediaQueryList);
  const container = document.createElement("div");
  const geometry = { top: 0, writes: 0 };
  Object.defineProperties(container, {
    clientHeight: { get: () => 400 },
    clientWidth: { get: () => 600 },
    scrollTop: {
      get: () => geometry.top,
      set: (value: number) => {
        geometry.writes++;
        geometry.top = value;
      },
    },
  });
  container.getBoundingClientRect = () =>
    Object.assign(new DOMRect(0, 40, 600, 400), { top: 40, bottom: 440 });
  for (let i = 0; i < count * 2; i++) {
    const row = document.createElement("div");
    row.className = "message-row";
    row.dataset.messageIndex = String(i);
    row.getBoundingClientRect = () =>
      Object.assign(new DOMRect(0, 40 + i * 80 - geometry.top, 600, 80), {
        top: 40 + i * 80 - geometry.top,
        bottom: 120 + i * 80 - geometry.top,
      });
    container.append(row);
  }
  document.body.append(container);
  const markers: ChatScrollMarker[] = Array.from({ length: count }, (_, i) => ({
    id: `question-${i}`,
    index: i * 2,
    messageIndex: i * 2,
    ordinal: i + 1,
    topPct: (i / Math.max(1, count - 1)) * 100,
    preview: `Question ${i + 1}`,
    replyPreview: `Answer ${i + 1}`,
    inherited: i < 2,
  }));
  const wrapper = mount(ChatScrollMarkers, {
    props: {
      markers,
      container,
      height,
      right: 14,
      loading: false,
      isDark: false,
    },
    attachTo: document.body,
  });
  wrappers.push(wrapper);
  return { wrapper, container, markers, geometry };
}

describe("conversation scroll markers", () => {
  beforeEach(() => {
    frames.clear();
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
      frames.set(++frameId, callback);
      return frameId;
    });
    vi.spyOn(window, "cancelAnimationFrame").mockImplementation((id) =>
      frames.delete(id),
    );
  });
  afterEach(() => {
    wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
    vi.restoreAllMocks();
    document.body.innerHTML = "";
  });

  it("previews a question and delegates its unchanged jump target without scrolling", async () => {
    const { wrapper, markers, geometry } = mountMarkers();
    await wrapper.findAll(".scroll-marker-dot")[3].trigger("mouseenter");
    expect(wrapper.get('[role="tooltip"]').text()).toContain("Question 4");
    expect(wrapper.get(".marker-preview-reply").text()).toBe("Answer 4");
    await wrapper.findAll(".scroll-marker-dot")[3].trigger("click");
    expect(wrapper.emitted("jump")).toEqual([[markers[3]]]);
    expect(geometry.writes).toBe(0);
  });

  it("shows the current question and reply when its tick represents a dense group", async () => {
    const { wrapper, container, geometry } = mountMarkers(1200, 244);
    geometry.top = 55 * 160;
    container.dispatchEvent(new Event("scroll"));
    frames.forEach((callback) => callback(performance.now()));
    frames.clear();
    await nextTick();
    await wrapper.get(".scroll-marker-dot--current").trigger("mouseenter");
    expect(wrapper.get(".marker-preview-title").text()).toBe("Question 56");
    expect(wrapper.get(".marker-preview-reply").text()).toBe("Answer 56");
  });

  it("paginates long groups in batches of twenty without dropping any question", async () => {
    const { wrapper, geometry } = mountMarkers(1200, 244);
    await wrapper.findAll(".scroll-marker-dot")[0].trigger("click");
    const numbers: number[] = [];
    while (true) {
      const choices = wrapper.findAll(".marker-choice");
      expect(choices.length).toBeLessThanOrEqual(20);
      numbers.push(
        ...choices.map((choice) =>
          Number(choice.get(".marker-choice-number").text()),
        ),
      );
      const next = wrapper.get('button[aria-label="下一页"]');
      if (next.attributes("disabled") !== undefined) break;
      await next.trigger("click");
    }
    expect(numbers).toEqual(Array.from({ length: 67 }, (_, i) => i + 1));
    expect(geometry.writes).toBe(0);
    await wrapper.get('button[aria-label="上一页"]').trigger("click");
    await wrapper.findAll(".marker-choice")[0].trigger("click");
    expect(wrapper.emitted("jump")?.[0][0]).toMatchObject({
      ordinal: 41,
      index: 80,
    });
  });

  it("opens on the page containing the current turn and keeps response updates live", async () => {
    const { wrapper, markers, container, geometry } = mountMarkers(1200, 244);
    geometry.top = 55 * 160;
    container.dispatchEvent(new Event("scroll"));
    frames.forEach((callback) => callback(performance.now()));
    frames.clear();
    await nextTick();
    await wrapper.get(".scroll-marker-dot--current").trigger("click");
    expect(wrapper.get(".marker-choice-pager").text()).toContain("3 / 4 页");
    expect(wrapper.get('.marker-choice[aria-current="true"]').text()).toContain(
      "Answer 56",
    );
    await wrapper.setProps({
      markers: markers.map((marker) =>
        marker.id === "question-55"
          ? { ...marker, replyPreview: "Updated streamed reply" }
          : marker,
      ),
    });
    expect(wrapper.get('.marker-choice[aria-current="true"]').text()).toContain(
      "Updated streamed reply",
    );
    expect(wrapper.get(".marker-choice-pager").text()).toContain("3 / 4 页");
  });

  it("distinguishes an unfinished reply from a turn without reply text", async () => {
    const { wrapper, markers } = mountMarkers();
    await wrapper.setProps({
      markers: [{ ...markers[0], replyPreview: "", replyLoading: true }],
    });
    await wrapper.get(".scroll-marker-dot").trigger("mouseenter");
    expect(wrapper.get(".marker-preview-reply").text()).toBe("回复生成中…");
    await wrapper.setProps({
      markers: [{ ...markers[0], replyPreview: "", replyLoading: false }],
    });
    expect(wrapper.get(".marker-preview-reply").text()).toBe("暂无回复正文");
  });

  it("keeps every dense question selectable, including inherited boundaries", async () => {
    const { wrapper, markers } = mountMarkers(120, 244);
    const buttons = wrapper.findAll(".scroll-marker-dot");
    expect(buttons.length).toBeLessThan(15);
    const ids: Array<string | number> = [];
    for (const button of buttons) {
      await button.trigger("click");
      for (const choice of wrapper.findAll(".marker-choice")) {
        const number = Number(choice.get(".marker-choice-number").text());
        ids.push(markers[number - 1].id);
      }
      await wrapper.get(".marker-close").trigger("click");
    }
    expect(ids).toEqual(markers.map((marker) => marker.id));
    await buttons[0].trigger("click");
    expect(wrapper.findAll(".marker-choice-origin")).toHaveLength(2);
    await wrapper.findAll(".marker-choice")[3].trigger("click");
    expect(wrapper.emitted("jump")).toEqual([[markers[3]]]);
  });

  it("follows the visible message rather than the full-session scrollbar fraction", async () => {
    const { wrapper, container, geometry } = mountMarkers();
    await nextTick();
    expect(
      wrapper
        .get('.scroll-marker-dot[aria-current="true"]')
        .attributes("aria-label"),
    ).toContain("第 1 轮");
    geometry.top = 640;
    container.dispatchEvent(new Event("scroll"));
    frames.forEach((callback) => callback(performance.now()));
    frames.clear();
    await nextTick();
    expect(
      wrapper
        .get('.scroll-marker-dot[aria-current="true"]')
        .attributes("aria-label"),
    ).toContain("第 5 轮");
    expect(geometry.writes).toBe(0);
  });

  it("preserves an open question list through equivalent streaming marker updates", async () => {
    const { wrapper, markers, container } = mountMarkers(120, 244);
    await wrapper.findAll(".scroll-marker-dot")[0].trigger("click");
    await wrapper.setProps({
      markers: markers.map((marker) => ({ ...marker })),
    });
    container.dispatchEvent(new Event("scroll"));
    await nextTick();
    expect(wrapper.find('[role="dialog"]').exists()).toBe(true);
  });

  it("dismisses a group with Escape and restores focus without changing the viewport", async () => {
    const { wrapper, geometry } = mountMarkers(120, 244);
    const button = wrapper.findAll(".scroll-marker-dot")[0];
    await button.trigger("click");
    await wrapper.get(".marker-choice").trigger("keydown", { key: "Escape" });
    expect(wrapper.find(".scroll-marker-popover").exists()).toBe(false);
    expect(document.activeElement).toBe(button.element);
    expect(geometry.writes).toBe(0);
  });

  it("keeps choices open across window blur but dismisses them when focus leaves the navigation", async () => {
    const { wrapper } = mountMarkers(120, 244);
    await wrapper.findAll(".scroll-marker-dot")[0].trigger("click");
    await wrapper.trigger("focusout", { relatedTarget: null });
    expect(wrapper.find('[role="dialog"]').exists()).toBe(true);
    const outside = document.createElement("button");
    document.body.append(outside);
    await wrapper.trigger("focusout", { relatedTarget: outside });
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false);
  });

  it("closes stale previews on scrolling, resizing, outside input, and loading", async () => {
    const { wrapper, container } = mountMarkers();
    const button = wrapper.findAll(".scroll-marker-dot")[0];
    await button.trigger("mouseenter");
    container.dispatchEvent(new Event("scroll"));
    await nextTick();
    expect(wrapper.find(".scroll-marker-popover").exists()).toBe(false);
    await button.trigger("mouseenter");
    await wrapper.setProps({ height: 500 });
    expect(wrapper.find(".scroll-marker-popover").exists()).toBe(false);
    await button.trigger("mouseenter");
    document.body.dispatchEvent(
      new PointerEvent("pointerdown", { bubbles: true }),
    );
    await nextTick();
    expect(wrapper.find(".scroll-marker-popover").exists()).toBe(false);
    await button.trigger("mouseenter");
    await wrapper.setProps({ loading: true });
    expect(wrapper.find(".scroll-marker-popover").exists()).toBe(false);
    expect(wrapper.get('[role="status"]').text()).toContain("正在加载");
    await button.trigger("click");
    expect(wrapper.emitted("jump")).toBeUndefined();
  });

  it("supports the loaded-history fallback target without converting it to a global index", async () => {
    const { wrapper, markers } = mountMarkers();
    const fallback = { ...markers[0], index: -1 };
    await wrapper.setProps({ markers: [fallback] });
    await wrapper.get(".scroll-marker-dot").trigger("click");
    expect(wrapper.emitted("jump")).toEqual([[fallback]]);
  });

  it("groups more coarsely for touch while keeping all questions reachable", async () => {
    const { wrapper, markers } = mountMarkers(120, 244, true);
    await nextTick();
    expect(wrapper.classes()).toContain("is-coarse");
    expect(wrapper.findAll(".scroll-marker-dot").length).toBeLessThan(8);
    await wrapper.findAll(".scroll-marker-dot")[0].trigger("click");
    expect(wrapper.findAll(".marker-choice").length).toBeGreaterThan(1);
    await wrapper.findAll(".marker-choice").at(-1)!.trigger("click");
    const emitted = wrapper.emitted("jump")![0][0] as ChatScrollMarker;
    expect(markers).toContainEqual(emitted);
  });

  it("detaches its scroll listener when the chat panel is replaced or unmounted", async () => {
    const { wrapper, container } = mountMarkers();
    const detach = vi.spyOn(container, "removeEventListener");
    await wrapper.setProps({ container: null });
    expect(detach).toHaveBeenCalledWith("scroll", expect.any(Function));
    container.dispatchEvent(new Event("scroll"));
    expect(frames.size).toBe(0);
    wrapper.unmount();
  });
});
