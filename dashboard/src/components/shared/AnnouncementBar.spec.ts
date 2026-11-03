import { mount } from "@vue/test-utils";
import { ref } from "vue";
import { expect, it, vi } from "vitest";

const notice = ref({
  title: "Notice",
  content:
    "Before ![diagram](https://example.com/image.png) after\n\n![second][pic]\n\n[pic]: https://example.com/second.png",
});

vi.mock("@/composables/useAnnouncement", () => ({
  useAnnouncement: () => ({ data: notice, collapsed: ref(false) }),
}));
vi.mock("markstream-vue", () => ({
  MarkdownRender: {
    props: ["content"],
    template: '<div class="detail">{{ content }}</div>',
  },
}));

it("renders Markdown images in both copies of the scrolling announcement", () => {
  const wrapper = mount(AnnouncementBar, {
    global: {
      stubs: {
        VSystemBar: { template: "<div><slot /></div>" },
        VDialog: { template: "<div><slot /></div>" },
        VCard: { template: "<div><slot /></div>" },
        VCardTitle: { template: "<div><slot /></div>" },
        VCardText: { template: "<div><slot /></div>" },
        VCardActions: { template: "<div><slot /></div>" },
        VBtn: { template: "<button><slot /></button>" },
        VSpacer: true,
      },
    },
  });
  const copies = wrapper.findAll(".ann-text");
  expect(copies).toHaveLength(2);
  expect(copies[0].text()).toContain("Before");
  expect(copies[0].text()).toContain("after");
  expect(copies[0].findAll("img").map((img) => img.attributes("src"))).toEqual([
    "https://example.com/image.png",
    "https://example.com/second.png",
  ]);
  expect(copies[0].find("img").attributes("alt")).toBe("diagram");
  expect(copies[1].attributes("aria-hidden")).toBe("true");
  expect(wrapper.find(".detail").text()).toBe(notice.value.content);
});

import AnnouncementBar from "./AnnouncementBar.vue";
