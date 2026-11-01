import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defineComponent, h, ref } from "vue";
import { flushPromises, mount } from "@vue/test-utils";
import { resetAccountSession } from "@/utils/accountSession";
const deps = vi.hoisted(() => ({
  get: vi.fn(),
  start: vi.fn(),
  stop: vi.fn(),
}));
vi.mock("@/api/http", () => ({ httpClient: { get: deps.get } }));
vi.mock("vuetify", () => ({
  useTheme: () => ({ global: { current: { value: { dark: false } } } }),
}));
vi.mock("@/composables/useVADRecording", () => ({
  useVADRecording: () => ({
    startRecording: deps.start,
    stopRecording: deps.stop,
    isSpeaking: ref(false),
    audioEnergy: ref(0),
  }),
}));
import LiveMode from "./LiveMode.vue";
class Socket {
  static OPEN = 1;
  static instances: Socket[] = [];
  readyState = 1;
  send = vi.fn();
  close = vi.fn();
  onopen?: () => void;
  onmessage?: (event: { data: string }) => void;
  onclose?: (event: { code: number }) => void;
  onerror?: () => void;
  constructor() {
    Socket.instances.push(this);
  }
  emit(message: unknown) {
    this.onmessage?.({ data: JSON.stringify(message) });
  }
}
const Button = defineComponent({
  setup(_, { attrs, slots }) {
    return () => h("button", attrs, slots.default?.());
  },
});
const sources: Array<{
  start: ReturnType<typeof vi.fn>;
  stop: ReturnType<typeof vi.fn>;
  disconnect: ReturnType<typeof vi.fn>;
}> = [];
let finishDecode!: (value: unknown) => void;
let audio: {
  close: ReturnType<typeof vi.fn>;
  decodeAudioData: () => Promise<unknown>;
  createBufferSource: () => unknown;
};
beforeEach(() => {
  vi.resetAllMocks();
  resetAccountSession();
  localStorage.setItem("token", "current-token");
  Socket.instances = [];
  sources.length = 0;
  vi.stubGlobal("WebSocket", Socket);
  vi.stubGlobal("requestAnimationFrame", vi.fn());
  vi.stubGlobal("cancelAnimationFrame", vi.fn());
  vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:owned");
  vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
  vi.spyOn(console, "log").mockImplementation(() => {});
  vi.spyOn(console, "error").mockImplementation(() => {});
  audio = {
    close: vi.fn().mockResolvedValue(undefined),
    decodeAudioData: vi.fn(
      () =>
        new Promise((resolve) => {
          finishDecode = resolve;
        }),
    ),
    createBufferSource: vi.fn(() => {
      const source = {
        start: vi.fn(),
        stop: vi.fn(),
        disconnect: vi.fn(),
        connect: vi.fn(),
        onended: null,
      };
      sources.push(source);
      return source;
    }),
  };
  vi.stubGlobal(
    "AudioContext",
    class {
      constructor() {
        return {
          ...audio,
          createAnalyser: () => ({ frequencyBinCount: 1, connect: vi.fn() }),
          destination: {},
        };
      }
    },
  );
  deps.start.mockResolvedValue(undefined);
  deps.get.mockResolvedValue({ data: new Blob(["private"]) });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
async function setup() {
  const wrapper = mount(LiveMode, {
    props: {
      sessionId: "owned-session",
      configId: "research",
      selectedProvider: "gpt-mini",
      selectedModel: "mini",
      thinkingEffort: "high",
    },
    global: { stubs: { VBtn: Button, SiriOrb: true } },
  });
  await wrapper.get(".center-circle-container").trigger("click");
  const socket = Socket.instances[0];
  socket.onopen?.();
  expect(JSON.parse(socket.send.mock.calls[0][0])).toEqual({
    t: "bind",
    session_id: "owned-session",
    config_id: "research",
    selected_provider: "gpt-mini",
    selected_model: "mini",
    thinking_effort: "high",
  });
  expect(deps.start).not.toHaveBeenCalled();
  socket.emit({ t: "session_bound", session_id: "owned-session" });
  await flushPromises();
  return { wrapper, socket };
}
describe("live transport media lifecycle", () => {
  it("renders authenticated attachments and still decodes streamed audio", async () => {
    const { wrapper, socket } = await setup();
    expect(deps.start).toHaveBeenCalled();
    for (const type of ["image", "record", "video", "file"])
      socket.emit({
        t: "attachment",
        data: { type, attachment_id: type, filename: `${type}.bin` },
      });
    socket.emit({ t: "bot_text_chunk", data: { text: "private answer" } });
    socket.emit({ t: "response", data: btoa("audio") });
    await flushPromises();
    finishDecode({});
    await flushPromises();
    expect(deps.get).toHaveBeenCalledTimes(4);
    expect(wrapper.find("img").attributes("src")).toBe("blob:owned");
    expect(wrapper.find("audio").exists()).toBe(true);
    expect(wrapper.find("video").exists()).toBe(true);
    expect(wrapper.findAll("a")).toHaveLength(4);
    expect(wrapper.text()).toContain("private answer");
    expect(sources[0].start).toHaveBeenCalled();
    wrapper.unmount();
    await flushPromises();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:owned");
    expect(audio.close).toHaveBeenCalled();
    expect(socket.close).toHaveBeenCalled();
  });
  it.each(["close", "account"])(
    "discards late attachment and audio callbacks after %s",
    async (action) => {
      const { wrapper, socket } = await setup();
      let finish!: (value: unknown) => void;
      deps.get.mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finish = resolve;
          }),
      );
      socket.emit({
        t: "attachment",
        data: { type: "file", attachment_id: "late", filename: "secret" },
      });
      socket.emit({ t: "response", data: btoa("audio") });
      await flushPromises();
      const signal = deps.get.mock.calls[0][1].signal;
      if (action === "account") resetAccountSession();
      wrapper.unmount();
      finish({ data: new Blob(["late-secret"]) });
      finishDecode({});
      await flushPromises();
      expect(signal.aborted).toBe(true);
      expect(URL.createObjectURL).not.toHaveBeenCalled();
      expect(sources).toHaveLength(0);
      expect(deps.stop).toHaveBeenCalled();
    },
  );
});
