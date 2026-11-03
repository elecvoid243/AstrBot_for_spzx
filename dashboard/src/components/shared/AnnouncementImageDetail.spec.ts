import { mount } from "@vue/test-utils";
import { ref } from "vue";
import { expect, it, vi } from "vitest";

vi.mock("@/composables/useAnnouncement", () => ({
  useAnnouncement: () => ({
    data: ref({
      title: "Notice",
      content:
        "![Uploaded image](https://example.com/announcement/images/test.png)",
    }),
    collapsed: ref(false),
  }),
}));

import AnnouncementBar from "./AnnouncementBar.vue";

it("renders the uploaded image in the announcement details", async () => {
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
  await wrapper.find(".ann-middle").trigger("click");
  await vi.waitFor(() => {
    const image = wrapper.find(".ann-dialog-markdown img");
    expect(image.exists()).toBe(true);
    expect(image.attributes("src")).toBe(
      "https://example.com/announcement/images/test.png",
    );
  });
});
