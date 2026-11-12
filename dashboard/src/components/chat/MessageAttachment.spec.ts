import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import MessageAttachment from "./MessageAttachment.vue";

const filePart = {
  type: "file",
  filename: "guide.docx",
  attachment_id: "att-1",
};

const buttonStub = {
  template: "<button class='stub-btn' />",
};

describe("MessageAttachment download affordance", () => {
  it("shows the download button for a local file part without an explicit prop", () => {
    const wrapper = mount(MessageAttachment, {
      props: { part: filePart },
      global: { stubs: { "v-btn": buttonStub } },
    });

    expect(wrapper.find(".stub-btn").exists()).toBe(true);
  });

  it("hides the download button when the caller opts out", () => {
    const wrapper = mount(MessageAttachment, {
      props: { part: filePart, downloadable: false },
      global: { stubs: { "v-btn": buttonStub } },
    });

    expect(wrapper.find(".stub-btn").exists()).toBe(false);
  });
});
