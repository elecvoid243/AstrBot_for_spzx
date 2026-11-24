import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, expect, it, vi } from "vitest";
import axios from "axios";
import ProjectTodoNotices from "./ProjectTodoNotices.vue";

const push = vi.hoisted(() => vi.fn());
vi.mock("vue-router", () => ({
  useRoute: () => ({ path: "/chat" }),
  useRouter: () => ({ push }),
}));
vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({ tm: (key: string) => key }),
  useI18n: () => ({ locale: { value: "en-US" } }),
}));
afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

it("keeps each occurrence on screen until its own close action succeeds", async () => {
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
  const first = {
    id: "a",
    project_id: "p",
    project: "Project",
    title: "Review",
    description: "Verify both themes",
    unread_at: 100,
  };
  const second = { ...first, id: "b", title: "Release" };
  let unread = [first, second];
  vi.spyOn(axios, "get").mockImplementation(async () => ({
    data: { data: unread },
  }));
  const post = vi.spyOn(axios, "post").mockImplementation(async () => {
    unread = [second];
    return {};
  });
  const wrap = mount(ProjectTodoNotices, { attachTo: document.body });
  try {
    await flushPromises();
    const card = wrap.get("article").element;
    expect(card.textContent).toContain(first.description);
    document.body.click();
    await wrap.get(".details").trigger("click");
    expect(push).toHaveBeenCalledWith({
      path: "/chat",
      query: { boardProject: "p", boardCard: "a" },
    });
    expect(post).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(30000);
    await flushPromises();
    expect(wrap.get("article").element).toBe(card);
    await wrap.get(".dismiss").trigger("click");
    await flushPromises();
    expect(post).toHaveBeenCalledWith("/api/project-todos/p/a/read", null, {
      params: { at: 100 },
    });
    expect(wrap.findAll("article")).toHaveLength(1);
    expect(wrap.get("h3").text()).toBe("Release");
    const previous = wrap.get("article").element;
    unread = [{ ...second, unread_at: 200 }];
    window.dispatchEvent(new Event("astrbot-board-change"));
    await flushPromises();
    expect(wrap.get("article").element).not.toBe(previous);
  } finally {
    wrap.unmount();
  }
});
