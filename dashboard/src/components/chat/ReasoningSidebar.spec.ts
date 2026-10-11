import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mount, type VueWrapper } from "@vue/test-utils";
import { nextTick } from "vue";
import ReasoningSidebar from "./ReasoningSidebar.vue";

const timeline = vi.hoisted(() => ({ file: vi.fn(), text: vi.fn() }));
vi.mock("@/components/chat/message_list_comps/ReasoningTimeline.vue", () => ({
  default: {
    name: "ReasoningTimeline",
    template: '<div><button type="button">Expand</button></div>',
    methods: { scrollToFile: timeline.file, scrollToText: timeline.text },
  },
}));

const frames = new Map<number, FrameRequestCallback>();
const wrappers: VueWrapper[] = [];
let frameId = 0;

async function mountSidebar(props: Record<string, unknown> = {}) {
  const wrapper = mount(ReasoningSidebar, {
    props: { modelValue: true, parts: [], reasoning: "thinking", ...props },
    global: { stubs: { "v-btn": true } },
  });
  wrappers.push(wrapper);
  await nextTick();
  const body = wrapper.get(".reasoning-sidebar-body");
  const geometry = { height: 900, top: 120, writes: 0 };
  Object.defineProperties(body.element, {
    scrollHeight: { get: () => geometry.height },
    clientHeight: { get: () => 100 },
    scrollTop: {
      get: () => geometry.top,
      set: (value: number) => {
        geometry.writes++;
        geometry.top = Math.max(0, Math.min(value, geometry.height - 100));
      },
    },
  });
  return { wrapper, body, geometry };
}

function flushFrames() {
  const callbacks = [...frames.values()];
  frames.clear();
  callbacks.forEach((callback) => callback(performance.now()));
}

describe("reasoning sidebar scrolling", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    frames.clear();
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
      frames.set(++frameId, callback);
      return frameId;
    });
    vi.spyOn(window, "cancelAnimationFrame").mockImplementation((id) => {
      frames.delete(id);
    });
  });

  afterEach(() => {
    wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
    vi.restoreAllMocks();
    document.body.innerHTML = "";
  });

  it("follows new activity with at most one scroll per frame", async () => {
    const { wrapper, geometry } = await mountSidebar();
    flushFrames();
    expect(geometry.top).toBe(800);
    geometry.height = 1100;
    await wrapper.setProps({ reasoning: "thinking more" });
    await wrapper.setProps({ parts: [{ type: "think", think: "new step" }] });
    expect(frames.size).toBe(1);
    flushFrames();
    expect(geometry.top).toBe(1000);
    expect(geometry.writes).toBe(2);
  });

  it("cancels a pending follow on upward input and resumes at bottom", async () => {
    const { wrapper, body, geometry } = await mountSidebar();
    flushFrames();
    geometry.height = 1100;
    await wrapper.setProps({ reasoning: "update before wheel" });
    await body.trigger("wheel", { deltaY: -20 });
    flushFrames();
    expect(geometry.top).toBe(800);
    geometry.top = 500;
    await body.trigger("scroll");
    await wrapper.setProps({ reasoning: "still streaming" });
    flushFrames();
    expect(geometry.top).toBe(500);
    await body.trigger("wheel", { deltaY: 500 });
    geometry.top = 1000;
    await body.trigger("scroll");
    geometry.height = 1200;
    await wrapper.setProps({ reasoning: "follow again" });
    flushFrames();
    expect(geometry.top).toBe(1100);
  });

  it.each(["PageUp", "Home", "ArrowUp"])(
    "pauses follow for %s",
    async (key) => {
      const { wrapper, body, geometry } = await mountSidebar();
      flushFrames();
      await body.trigger("keydown", { key });
      geometry.top = 400;
      geometry.height = 1100;
      await wrapper.setProps({ reasoning: "keyboard reading" });
      flushFrames();
      expect(geometry.top).toBe(400);
    },
  );

  it("ignores keys on interactive children and Ctrl-wheel zoom", async () => {
    const { wrapper, body, geometry } = await mountSidebar();
    flushFrames();
    await body.get("button").trigger("keydown", { key: "Home" });
    await body.trigger("wheel", { ctrlKey: true, deltaY: -100 });
    geometry.height = 1100;
    await wrapper.setProps({ reasoning: "keep following" });
    flushFrames();
    expect(geometry.top).toBe(1000);
  });

  it("pauses follow when scrolling up by touch or dragging the scrollbar", async () => {
    const { wrapper, body, geometry } = await mountSidebar();
    flushFrames();
    await body.trigger("touchstart", { touches: [{ clientY: 100 }] });
    await body.trigger("touchmove", { touches: [{ clientY: 150 }] });
    geometry.top = 350;
    await body.trigger("scroll");
    geometry.height = 1100;
    await wrapper.setProps({ reasoning: "touch reading" });
    flushFrames();
    expect(geometry.top).toBe(350);
    await body.trigger("wheel", { deltaY: 1000 });
    geometry.top = 1000;
    await body.trigger("scroll");
    await body.trigger("pointerdown");
    geometry.height = 1200;
    await wrapper.setProps({ reasoning: "scrollbar held" });
    flushFrames();
    expect(geometry.top).toBe(1000);
  });

  it("keeps following when a collapsed block clamps the viewport to bottom", async () => {
    const { wrapper, body, geometry } = await mountSidebar();
    flushFrames();
    geometry.height = 300;
    geometry.top = 200;
    await body.trigger("scroll");
    geometry.height = 400;
    await wrapper.setProps({ reasoning: "activity after collapse" });
    flushFrames();
    expect(geometry.top).toBe(300);
  });

  it.each([
    ["focusCallId", "file-call", timeline.file],
    ["focusQuery", "keyword", timeline.text],
  ] as const)(
    "keeps %s positioning ahead of automatic follow",
    async (prop, value, focus) => {
      const { wrapper, body, geometry } = await mountSidebar({ [prop]: value });
      await nextTick();
      expect(focus).toHaveBeenCalledWith(value);
      geometry.top = 230;
      await body.trigger("scroll");
      geometry.height = 1200;
      await wrapper.setProps({ reasoning: "new content after locate" });
      flushFrames();
      expect(geometry.top).toBe(230);
      await body.trigger("wheel", { deltaY: 1000 });
      geometry.top = 1100;
      await body.trigger("scroll");
      geometry.height = 1300;
      await wrapper.setProps({ reasoning: "user follows again" });
      flushFrames();
      expect(geometry.top).toBe(1200);
    },
  );

  it("cancels scheduled scrolling on close and unmount", async () => {
    const { wrapper, geometry } = await mountSidebar();
    expect(frames.size).toBe(1);
    await wrapper.setProps({ modelValue: false });
    expect(frames.size).toBe(0);
    flushFrames();
    expect(geometry.top).toBe(120);
    await wrapper.setProps({ modelValue: true });
    expect(frames.size).toBe(1);
    wrapper.unmount();
    expect(frames.size).toBe(0);
  });
});
