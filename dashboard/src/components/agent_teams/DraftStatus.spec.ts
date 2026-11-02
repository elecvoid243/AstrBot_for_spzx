import { mount, flushPromises } from "@vue/test-utils";
import { ref } from "vue";
import { expect, it, vi } from "vitest";
import DraftStatus from "./DraftStatus.vue";
vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({
    tm: (key: string, params?: any) =>
      params ? `${key}:${params.old}:${params.current}` : key,
  }),
}));
it("reviews versions and cancel leaves the draft unchanged", async () => {
  const draft: any = {
    status: ref("conflict"),
    baseVersion: ref(1),
    reviewed: ref(null),
    retry: vi.fn(),
    rebase: vi.fn(),
    reviewPublished: vi.fn(),
  };
  draft.reviewPublished.mockImplementation(async () => {
    draft.reviewed.value = { version: 2, definition: { name: "Latest" } };
  });
  const wrapper = mount(DraftStatus, {
    props: { draft },
    global: {
      stubs: {
        "v-alert": { template: "<div><slot /></div>" },
        "v-btn": { template: "<button><slot /></button>" },
      },
    },
  });
  await wrapper
    .findAll("button")
    .find((button) => button.text() === "draft.review")!
    .trigger("click");
  await flushPromises();
  expect(wrapper.text()).toContain("draft.baseComparison:1:2");
  expect(wrapper.text()).toContain("Latest");
  await wrapper
    .findAll("button")
    .find((button) => button.text() === "v2.close")!
    .trigger("click");
  expect(draft.rebase).not.toHaveBeenCalled();
  expect(wrapper.emitted("useLatest")).toBeUndefined();
  expect(draft.status.value).toBe("conflict");
});
