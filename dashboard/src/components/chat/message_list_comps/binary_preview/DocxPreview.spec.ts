import { expect, it, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
vi.mock("mammoth/mammoth.browser", () => ({
  convertToHtml: async () => ({
    value:
      '<p>Document content</p><a href="javascript:alert(1)">unsafe</a><img src="x" onerror="alert(1)">',
  }),
}));
import DocxPreview from "./DocxPreview.vue";

it("keeps document content while sanitizing generated host-document HTML", async () => {
  const blob = new Blob(["docx"]);
  blob.arrayBuffer = async () => new ArrayBuffer(4);
  const wrapper = mount(DocxPreview, { props: { blob } });
  await flushPromises();
  expect(wrapper.text()).toContain("Document content");
  expect(wrapper.find("a").attributes("href")).toBeUndefined();
  expect(wrapper.find("img").attributes("onerror")).toBeUndefined();
  wrapper.unmount();
});
