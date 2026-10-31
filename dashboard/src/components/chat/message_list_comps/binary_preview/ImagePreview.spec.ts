// Author: elecvoid243, 2026-07-31
// ImagePreview — object URL lifecycle + resolution caption.
// jsdom does not implement URL.createObjectURL, so both URL helpers
// are stubbed before importing the component.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";

const mockCreateObjectURL = vi.fn(() => "blob:mock-url");
const mockRevokeObjectURL = vi.fn();
URL.createObjectURL = mockCreateObjectURL;
URL.revokeObjectURL = mockRevokeObjectURL;

import ImagePreview from "./ImagePreview.vue";

const stubs = {
  "v-icon": { template: '<i class="icon" />' },
};

function makePng(): Blob {
  return new Blob([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], {
    type: "image/png",
  });
}

beforeEach(() => {
  mockCreateObjectURL.mockClear();
  mockRevokeObjectURL.mockClear();
});

describe("ImagePreview", () => {
  it("renders an img bound to a fresh object URL", () => {
    const wrapper = mount(ImagePreview, {
      props: { blob: makePng(), filename: "logo.png" },
      global: { stubs },
    });
    const img = wrapper.get("img");
    expect(img.attributes("src")).toBe("blob:mock-url");
    expect(img.attributes("alt")).toBe("logo.png");
    expect(mockCreateObjectURL).toHaveBeenCalledTimes(1);
  });

  it("revokes the object URL on unmount", () => {
    const wrapper = mount(ImagePreview, {
      props: { blob: makePng(), filename: "logo.png" },
      global: { stubs },
    });
    wrapper.unmount();
    expect(mockRevokeObjectURL).toHaveBeenCalledWith("blob:mock-url");
  });

  it("shows the resolution caption after the img load event", async () => {
    const wrapper = mount(ImagePreview, {
      props: { blob: makePng(), filename: "logo.png" },
      global: { stubs },
    });
    expect(wrapper.find(".image-preview__caption").exists()).toBe(false);
    await wrapper.get("img").trigger("load");
    expect(wrapper.find(".image-preview__caption").exists()).toBe(true);
    // jsdom reports naturalWidth/naturalHeight as 0; the caption format is what matters.
    expect(wrapper.find(".image-preview__caption").text()).toBe("0 × 0");
  });

  it("shows the error placeholder when the img fails to decode", async () => {
    const wrapper = mount(ImagePreview, {
      props: { blob: makePng(), filename: "logo.png" },
      global: { stubs },
    });
    await wrapper.get("img").trigger("error");
    expect(wrapper.find(".image-preview__error").exists()).toBe(true);
    expect(wrapper.find("img").exists()).toBe(false);
  });
});

it("releases the previous image URL when a new blob arrives", async () => {
  const wrapper = mount(ImagePreview, {
    props: { blob: new Blob(["old"]), filename: "old.png" },
  });
  const previous = wrapper.find("img").attributes("src");
  await wrapper.setProps({ blob: new Blob(["new"]), filename: "new.png" });
  expect(URL.revokeObjectURL).toHaveBeenCalledWith(previous);
  wrapper.unmount();
});
