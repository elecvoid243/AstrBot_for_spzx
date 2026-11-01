import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defineComponent, h } from "vue";
import { flushPromises, mount } from "@vue/test-utils";
import { useVADRecording } from "./useVADRecording";

beforeEach(() => {
  vi.spyOn(console, "log").mockImplementation(() => {});
});
afterEach(() => {
  vi.restoreAllMocks();
});
describe("live recording initialization", () => {
  it.each(["close", "stop"])(
    "pauses a late microphone instance after %s",
    async (action) => {
      let finish!: (value: {
        start: ReturnType<typeof vi.fn>;
        pause: ReturnType<typeof vi.fn>;
        listening: boolean;
      }) => void;
      const instance = { start: vi.fn(), pause: vi.fn(), listening: false };
      window.vad = {
        MicVAD: {
          new: vi.fn(
            () =>
              new Promise((resolve) => {
                finish = resolve;
              }),
          ),
        },
      };
      let recording!: ReturnType<typeof useVADRecording>;
      const wrapper = mount(
        defineComponent({
          setup() {
            recording = useVADRecording();
            return () => h("div");
          },
        }),
      );
      const speech = vi.fn();
      const pending = recording.startRecording(speech, speech);
      if (action === "close") wrapper.unmount();
      else recording.stopRecording();
      finish(instance);
      await pending;
      await flushPromises();
      expect(instance.start).not.toHaveBeenCalled();
      expect(instance.pause).toHaveBeenCalled();
      expect(recording.isRecording.value).toBe(false);
      if (action !== "close") wrapper.unmount();
    },
  );
});
