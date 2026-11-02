import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
const { get, post, fetchStream, createSession } = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  fetchStream: vi.fn(),
  createSession: vi.fn(),
}));
vi.mock("@/api/v1", () => ({
  pluginExtensionApi: { get, post },
  browserControlApi: { createSession },
}));
vi.mock("@/api/http", () => ({ fetchWithAuth: fetchStream }));
import AgentBrowserView from "./AgentBrowserView.vue";

const current = {
  available: true,
  generation: "run-1",
  browser_session_id: "browser-1",
  tab_id: "tab-1",
};
const ok = (data: unknown) => ({ data: { status: "ok", data } });
let stream: ReadableStreamDefaultController<Uint8Array>;
beforeEach(() => {
  get.mockReset();
  post.mockReset();
  fetchStream.mockReset();
  createSession.mockReset();
  fetchStream.mockImplementation(async () => ({
    ok: true,
    body: new ReadableStream<Uint8Array>({
      start(controller) {
        stream = controller;
      },
    }),
  }));
});
afterEach(() => {
  vi.useRealTimers();
});

describe("Agent browser snapshots", () => {
  it.each(["pagehide", "astrbot:account-changed"])(
    "discards unconfirmed and queued clicks on %s and shows server-owned occupancy",
    async (exitEvent) => {
      class Socket {
        static OPEN = 1;
        static instance: Socket;
        readyState = 1;
        sent: any[] = [];
        onmessage?: (event: { data: string }) => void;
        onclose?: () => void;
        constructor() {
          Socket.instance = this;
        }
        send(raw: string) {
          this.sent.push(JSON.parse(raw));
        }
        close() {
          this.readyState = 3;
        }
      }
      vi.stubGlobal("WebSocket", Socket);
      const frame = {
        ...current,
        sequence: 1,
        control_epoch: 2,
        control_mode: "human",
        execution_state: "active",
        manual_control_version: 1,
        image: "pixels",
        mime_type: "image/jpeg",
        width: 1280,
        height: 720,
        viewport_width: 1280,
        viewport_height: 720,
      };
      get.mockResolvedValue(ok(frame));
      createSession.mockResolvedValue({
        data: { ticket: "ticket", websocket_path: "/control" },
      });
      const view = mount(AgentBrowserView, {
        props: { chatId: "chat-1", visible: true },
      });
      try {
        await flushPromises();
        expect(view.find(".agent-browser-occupied").exists()).toBe(true);
        expect(
          view.get(".agent-browser-takeover").attributes("disabled"),
        ).toBeDefined();
        stream.enqueue(
          new TextEncoder().encode(
            `data: ${JSON.stringify({
              ...frame,
              type: "state",
              sequence: 2,
              control_mode: "paused",
            })}\n\n`,
          ),
        );
        await flushPromises();
        await view.get(".agent-browser-takeover").trigger("click");
        await flushPromises();
        const socket = Socket.instance;
        socket.onmessage?.({
          data: JSON.stringify({
            operation: "acquire",
            status: "applied",
            control_epoch: 2,
          }),
        });
        await flushPromises();
        const image = view.get("img");
        vi.spyOn(image.element, "getBoundingClientRect").mockReturnValue({
          left: 0,
          top: 0,
          width: 1280,
          height: 720,
          right: 1280,
          bottom: 720,
        } as DOMRect);
        await image.trigger("click", { clientX: 100, clientY: 80, detail: 1 });
        await image.trigger("click", { clientX: 100, clientY: 80, detail: 1 });
        expect(
          socket.sent.filter((command) => command.operation === "click"),
        ).toHaveLength(1);
        window.dispatchEvent(new Event(exitEvent));
        await flushPromises();
        expect(socket.readyState).toBe(3);
        expect(view.attributes("data-human-control")).toBe("false");
        expect(view.attributes("data-pending-input")).toBe("0");
        expect(view.text()).toContain("输入确认丢失");
        socket.onmessage?.({
          data: JSON.stringify({
            operation: "click",
            status: "applied",
            input_id: 1,
            control_epoch: 2,
          }),
        });
        await flushPromises();
        expect(
          socket.sent.filter((command) => command.operation === "click"),
        ).toHaveLength(1);
        expect(view.attributes("data-human-control")).toBe("false");
        await view.get(".agent-browser-takeover").trigger("click");
        await flushPromises();
        const renewed = Socket.instance;
        renewed.onmessage?.({
          data: JSON.stringify({
            operation: "acquire",
            status: "applied",
            control_epoch: 2,
          }),
        });
        await flushPromises();
        await view.get(".agent-browser-takeover").trigger("click");
        stream.enqueue(
          new TextEncoder().encode(
            `data: ${JSON.stringify({
              ...frame,
              type: "state",
              sequence: 3,
              control_epoch: 3,
              control_mode: "paused",
            })}\n\n`,
          ),
        );
        await flushPromises();
        expect(renewed.readyState).toBe(1);
        expect(renewed.sent.at(-1).operation).toBe("release");
        renewed.onmessage?.({
          data: JSON.stringify({
            operation: "release",
            status: "applied",
            control_epoch: 3,
            state: { control_mode: "agent" },
          }),
        });
        await flushPromises();
        expect(view.text()).toContain("已交还控制权");
        expect(view.attributes("data-human-control")).toBe("false");
        await view.get(".agent-browser-takeover").trigger("click");
        await flushPromises();
        const revoked = Socket.instance;
        revoked.onmessage?.({
          data: JSON.stringify({
            operation: "acquire",
            status: "applied",
            control_epoch: 3,
          }),
        });
        get.mockRejectedValueOnce({ response: { status: 403 } });
        revoked.onclose?.();
        await flushPromises();
        expect(view.attributes("data-human-control")).toBe("false");
        expect(view.attributes("data-connection")).toBe("denied");
        expect(view.find("img").exists()).toBe(false);
      } finally {
        view.unmount();
        vi.unstubAllGlobals();
      }
    },
  );
  it("maps displayed CSS bounds and merges only adjacent wheel input until a loaded frame", async () => {
    class Socket {
      static OPEN = 1;
      static instance: Socket;
      readyState = 1;
      sent: Record<string, any>[] = [];
      onopen?: () => void;
      onmessage?: (event: { data: string }) => void;
      constructor() {
        Socket.instance = this;
      }
      send(raw: string) {
        this.sent.push(JSON.parse(raw));
      }
      close() {
        this.readyState = 3;
      }
      ack(operation: string, id?: number, status = "applied") {
        this.onmessage?.({
          data: JSON.stringify({
            operation,
            input_id: id,
            status,
            control_epoch: 2,
            state: { control_mode: "human" },
          }),
        });
      }
    }
    vi.stubGlobal("WebSocket", Socket);
    const frame = {
      ...current,
      manual_control_version: 1,
      keyboard_input_version: 1,
      pointer_input_version: 1,
      viewport_width: 1280,
      viewport_height: 720,
      document_id: "doc",
      scroll_x: 0,
      scroll_y: 0,
      position_revision: 0,
      control_epoch: 2,
      applied_input_id: 0,
      mime_type: "image/jpeg",
      image: "pixels",
      sequence: 1,
    };
    get.mockResolvedValueOnce(ok(frame)).mockResolvedValueOnce(ok(frame));
    createSession.mockResolvedValue({
      data: { ticket: "ticket", websocket_path: "/control" },
    });
    const view = mount(AgentBrowserView, {
      props: { chatId: "chat-1", visible: true },
      attachTo: document.body,
    });
    try {
      await flushPromises();
      const image = view.get("img");
      let decoded = true;
      Object.defineProperties(image.element, {
        currentSrc: { get: () => image.attributes("src") },
        complete: { get: () => decoded },
        naturalWidth: { get: () => 1280 },
      });
      vi.spyOn(image.element, "getBoundingClientRect").mockReturnValue({
        left: 10,
        top: 20,
        right: 650,
        bottom: 380,
        width: 640,
        height: 360,
      } as DOMRect);
      await image.trigger("load");
      await view.get(".agent-browser-takeover").trigger("click");
      await flushPromises();
      const socket = Socket.instance;
      socket.ack("acquire");
      await flushPromises();
      await image.trigger("click", { clientX: 330, clientY: 200, detail: 2 });
      expect(socket.sent.at(-1)).toMatchObject({
        operation: "click",
        click_count: 2,
        x: 640,
        y: 360,
        input_id: 1,
        target: { position_revision: 0 },
      });
      await image.trigger("wheel", {
        clientX: 330,
        clientY: 200,
        deltaX: 2,
        deltaY: 3,
        deltaMode: 1,
      });
      await image.trigger("wheel", {
        clientX: 330,
        clientY: 200,
        deltaX: 3,
        deltaY: 2,
        deltaMode: 1,
      });
      socket.ack("click", 1);
      await flushPromises();
      expect(view.attributes("data-awaiting-frame")).toBe("true");
      expect(socket.sent.filter((x) => x.operation === "wheel")).toHaveLength(
        0,
      );
      const priorLatency = view.attributes("data-frame-latency");
      for (const [index, type] of ["action", "state"].entries()) {
        stream.enqueue(
          new TextEncoder().encode(
            `data: ${JSON.stringify({
              ...frame,
              image: undefined,
              type,
              sequence: index + 2,
              applied_input_id: 1,
            })}\n\n`,
          ),
        );
        await flushPromises();
        expect(view.attributes("data-awaiting-frame")).toBe("true");
        expect(view.attributes("data-frame-input")).toBe("0");
        expect(view.attributes("data-frame-latency")).toBe(priorLatency);
        expect(socket.sent.filter((x) => x.operation === "wheel")).toHaveLength(
          0,
        );
      }
      // Late old-owner and old-browser captures cannot confirm current input.
      for (const patch of [
        { control_epoch: 1 },
        { browser_session_id: "old-browser" },
      ]) {
        stream.enqueue(
          new TextEncoder().encode(
            `data: ${JSON.stringify({
              ...frame,
              type: "frame",
              sequence: 4,
              applied_input_id: 99,
              width: 1280,
              height: 720,
              ...patch,
            })}\n\n`,
          ),
        );
        await flushPromises();
        expect(view.attributes("data-awaiting-frame")).toBe("true");
        expect(view.attributes("data-frame-input")).toBe("0");
        expect(view.attributes("data-frame-latency")).toBe(priorLatency);
        expect(socket.sent.filter((x) => x.operation === "wheel")).toHaveLength(
          0,
        );
      }
      // A real new capture with identical pixels needs no second DOM load.
      stream.enqueue(
        new TextEncoder().encode(
          `data: ${JSON.stringify({
            ...frame,
            type: "frame",
            sequence: 5,
            applied_input_id: 1,
            width: 1280,
            height: 720,
          })}\n\n`,
        ),
      );
      await flushPromises();
      expect(socket.sent.at(-1)).toMatchObject({
        operation: "wheel",
        delta_x: 80,
        delta_y: 80,
        input_id: 2,
      });
      socket.ack("wheel", 2, "stale");
      await flushPromises();
      expect(view.attributes("data-human-control")).toBe("true");
      expect(socket.sent.filter((x) => x.operation === "wheel")).toHaveLength(
        1,
      );
      socket.ack("reset_keys", 3);
      socket.ack("reset_keys", 4);
      decoded = false;
      stream.enqueue(
        new TextEncoder().encode(
          `data: ${JSON.stringify({
            ...frame,
            type: "frame",
            sequence: 6,
            image: "new-pixels",
            applied_input_id: 3,
            width: 1280,
            height: 720,
          })}\n\n`,
        ),
      );
      await flushPromises();
      expect(view.attributes("data-awaiting-frame")).toBe("true");
      decoded = true;
      await image.trigger("load");
      expect(view.attributes("data-awaiting-frame")).toBe("false");
      expect(socket.sent.filter((x) => x.operation === "wheel")).toHaveLength(
        1,
      );
      await image.trigger("wheel", {
        clientX: 330,
        clientY: 200,
        deltaX: 0,
        deltaY: 10,
        deltaMode: 0,
      });
      await new Promise((resolve) => setTimeout(resolve, 20));
      expect(socket.sent.filter((x) => x.operation === "wheel")).toHaveLength(
        2,
      );
      expect(socket.sent.at(-1)).not.toHaveProperty("queuedAt");
      await view.get(".agent-browser-fullscreen").trigger("click");
      await flushPromises();
      const fullNotice = document.querySelector(
        ".agent-browser-stage.is-fullscreen .agent-browser-notice",
      );
      const activeNotice = fullNotice?.textContent;
      socket.ack("wheel", Number(socket.sent.at(-1)?.input_id), "uncertain");
      await flushPromises();
      expect(view.attributes("data-human-control")).toBe("false");
      expect(fullNotice?.textContent?.trim().length).toBeGreaterThan(10);
      expect(fullNotice?.textContent).not.toBe(activeNotice);
    } finally {
      view.unmount();
      vi.unstubAllGlobals();
    }
  });

  it("orders text, commits composition once and drops queued content on blur", async () => {
    class ControlSocket {
      static OPEN = 1;
      static instance: ControlSocket;
      readyState = 1;
      sent: Record<string, unknown>[] = [];
      onopen?: () => void;
      onmessage?: (event: { data: string }) => void;
      onclose?: () => void;
      constructor() {
        ControlSocket.instance = this;
      }
      send(raw: string) {
        this.sent.push(JSON.parse(raw));
      }
      close() {
        this.readyState = 3;
      }
      ack(operation: string, mode = "human") {
        this.onmessage?.({
          data: JSON.stringify({
            operation,
            status: "applied",
            control_epoch: 2,
            state: { control_mode: mode },
          }),
        });
      }
    }
    vi.stubGlobal("WebSocket", ControlSocket);
    const frame = {
      ...current,
      manual_control_version: 1,
      keyboard_input_version: 1,
      image: "frame",
      sequence: 1,
    };
    get.mockResolvedValueOnce(ok(frame)).mockResolvedValueOnce(ok(frame));
    createSession.mockResolvedValue({
      data: {
        ticket: "private-ticket",
        websocket_path: "/api/v1/plugins/browser-control/ws",
      },
    });
    const view = mount(AgentBrowserView, {
      props: { chatId: "chat-1", visible: true },
      attachTo: document.body,
    });
    try {
      await flushPromises();
      await view.get(".agent-browser-takeover").trigger("click");
      await flushPromises();
      const socket = ControlSocket.instance;
      socket.onopen?.();
      socket.ack("acquire");
      await flushPromises();
      const sink = view.get("textarea");
      (sink.element as HTMLTextAreaElement).focus();
      await sink.trigger("beforeinput", { inputType: "insertText", data: "A" });
      await sink.trigger("compositionstart");
      await sink.trigger("beforeinput", {
        inputType: "insertCompositionText",
        data: "中",
        isComposing: true,
      });
      await sink.trigger("compositionend", { data: "中文" });
      await sink.trigger("beforeinput", {
        inputType: "insertText",
        data: "中文",
      });
      expect(socket.sent.filter((x) => x.operation === "text")).toHaveLength(1);
      socket.ack("text");
      socket.ack("reset_keys");
      expect(
        socket.sent.filter((x) => x.operation === "text").map((x) => x.text),
      ).toEqual(["A", "中文"]);
      await sink.trigger("beforeinput", {
        inputType: "insertText",
        data: "drop this",
      });
      await sink.trigger("blur");
      socket.ack("text");
      expect(socket.sent.at(-1)?.operation).toBe("reset_keys");
      expect(socket.sent.some((x) => x.text === "drop this")).toBe(false);
      socket.ack("reset_keys");
      await sink.trigger("beforeinput", {
        inputType: "insertText",
        data: "local",
      });
      expect(socket.sent.some((x) => x.text === "local")).toBe(false);
      await sink.trigger("focus");
      await sink.trigger("keydown", {
        code: "ShiftLeft",
        key: "Shift",
        shiftKey: true,
        getModifierState: () => false,
      });
      socket.ack("key");
      stream.enqueue(
        new TextEncoder().encode(
          `data: ${JSON.stringify({
            ...frame,
            type: "state",
            image: undefined,
            document_id: "next",
            sequence: 2,
          })}\n\n`,
        ),
      );
      await flushPromises();
      expect(socket.sent.at(-1)?.operation).toBe("reset_keys");
      socket.ack("reset_keys");
      socket.ack("reset_keys");
      await view.get(".agent-browser-takeover").trigger("click");
      socket.ack("reset_keys");
      expect(socket.sent.at(-1)?.operation).toBe("release");
      socket.ack("release", "agent");
      await flushPromises();
      expect(view.text()).toContain("已交还控制权");
      expect(view.text()).not.toContain("控制连接已断开");
      stream.enqueue(
        new TextEncoder().encode(
          `data: ${JSON.stringify({
            ...frame,
            type: "frame",
            image: "legacy",
            mime_type: "image/jpeg",
            width: 1280,
            height: 720,
            keyboard_input_version: undefined,
            document_id: "next",
            sequence: 3,
          })}\n\n`,
        ),
      );
      await flushPromises();
      await view.get(".agent-browser-takeover").trigger("click");
      await flushPromises();
      const legacySocket = ControlSocket.instance;
      legacySocket.onopen?.();
      legacySocket.ack("acquire");
      await flushPromises();
      expect(view.find("textarea").exists()).toBe(false);
      expect(view.text()).toContain("升级 Browser Use 后可使用键盘输入");
      await view.get(".agent-browser-takeover").trigger("click");
      expect(legacySocket.sent.at(-1)?.operation).toBe("release");
      expect(legacySocket.sent.some((x) => x.operation === "reset_keys")).toBe(
        false,
      );
      legacySocket.ack("release", "agent");
    } finally {
      view.unmount();
      vi.unstubAllGlobals();
    }
  });

  it("allows takeover during navigation before the next image arrives", async () => {
    const navigating = { ...current, manual_control_version: 1 };
    get
      .mockResolvedValueOnce(ok(navigating))
      .mockResolvedValueOnce(ok({ ...navigating, sequence: 1 }));
    createSession.mockImplementation(() => new Promise(() => {}));
    const view = mount(AgentBrowserView, {
      props: { chatId: "chat-1", visible: true },
    });
    await flushPromises();
    expect(view.find("img").exists()).toBe(false);
    expect(
      view.get(".agent-browser-takeover").attributes("disabled"),
    ).toBeUndefined();
    await view.get(".agent-browser-takeover").trigger("click");
    expect(createSession).toHaveBeenCalledWith({
      chat_id: "chat-1",
      generation: "run-1",
      browser_session_id: "browser-1",
    });
    expect(view.attributes("data-human-control")).toBe("false");
    view.unmount();
  });
  it("expands only the viewport without replacing its image or observation stream", async () => {
    get.mockResolvedValueOnce(ok(current)).mockResolvedValueOnce(
      ok({
        ...current,
        sequence: 1,
        image: "frame",
        mime_type: "image/jpeg",
        width: 1280,
        height: 720,
      }),
    );
    const view = mount(AgentBrowserView, {
      props: { chatId: "chat-1", visible: true },
    });
    document.body.appendChild(view.element);
    await flushPromises();
    const image = view.get("img").element;
    const fullscreenButton = view.get(".agent-browser-fullscreen");
    expect(view.get("details").attributes("open")).toBeUndefined();
    await fullscreenButton.trigger("click");
    await flushPromises();
    expect(document.activeElement).toBe(fullscreenButton.element);
    const tab = new KeyboardEvent("keydown", {
      key: "Tab",
      bubbles: true,
      cancelable: true,
    });
    document.dispatchEvent(tab);
    expect(tab.defaultPrevented).toBe(true);
    expect(
      document.querySelector(".agent-browser-stage.is-fullscreen img"),
    ).toBe(image);
    expect(fetchStream).toHaveBeenCalledTimes(1);
    const escape = new KeyboardEvent("keydown", {
      key: "Escape",
      bubbles: true,
      cancelable: true,
    });
    document.dispatchEvent(escape);
    await flushPromises();
    expect(escape.defaultPrevented).toBe(true);
    expect(view.get("img").element).toBe(image);
    expect(
      document.querySelector(".agent-browser-stage.is-fullscreen"),
    ).toBeNull();
    await fullscreenButton.trigger("click");
    await view.setProps({ visible: false });
    expect(
      document.querySelector(".agent-browser-stage.is-fullscreen"),
    ).toBeNull();
    view.unmount();
    view.element.remove();
  });

  it("finishes stop control without waiting for the observation stream to end", async () => {
    get
      .mockResolvedValueOnce(ok(current))
      .mockResolvedValueOnce(ok({ ...current, sequence: 1 }));
    post.mockResolvedValueOnce(
      ok({
        ...current,
        sequence: 2,
        execution_state: "stopped",
        current_action: null,
      }),
    );
    const view = mount(AgentBrowserView, {
      props: { chatId: "chat-1", visible: true },
    });
    await flushPromises();
    await view.get(".agent-browser-stop").trigger("click");
    await flushPromises();
    expect(view.attributes("data-connection")).toBe("stopped");
    post.mockResolvedValueOnce(
      ok({
        ...current,
        sequence: 3,
        available: false,
        execution_state: "closed",
      }),
    );
    await view.get(".agent-browser-close").trigger("click");
    await flushPromises();
    expect(post).toHaveBeenCalledTimes(2);
    expect(view.attributes("data-connection")).toBe("closed");
    view.unmount();
  });
  it("closes the real lifetime only after confirmed control and rejects stale callbacks", async () => {
    get
      .mockResolvedValueOnce(ok(current))
      .mockResolvedValueOnce(ok({ ...current, image: "pixels", sequence: 1 }));
    const view = mount(AgentBrowserView, {
      props: { chatId: "chat-1", visible: true },
    });
    await flushPromises();
    post.mockRejectedValueOnce(new Error("cleanup failed"));
    await view.get(".agent-browser-close").trigger("click");
    await flushPromises();
    expect(view.find('[role="alert"]').exists()).toBe(true);
    expect(view.find("img").exists()).toBe(true);
    post.mockResolvedValueOnce(
      ok({
        ...current,
        available: false,
        execution_state: "closed",
        current_action: null,
      }),
    );
    await view.get(".agent-browser-close").trigger("click");
    await flushPromises();
    expect(post).toHaveBeenLastCalledWith("browser-use/close", undefined, {
      params: {
        chat_id: "chat-1",
        generation: "run-1",
        browser_session_id: "browser-1",
      },
    });
    expect(view.attributes("data-connection")).toBe("closed");
    expect(view.find("img").exists()).toBe(false);
    view.unmount();
  });

  it("applies authoritative stopped actions and keeps the image available for inspection", async () => {
    get
      .mockResolvedValueOnce(ok(current))
      .mockResolvedValueOnce(ok({ ...current, image: "pixels", sequence: 1 }));
    const view = mount(AgentBrowserView, {
      props: { chatId: "chat-1", visible: true },
    });
    await flushPromises();
    stream.enqueue(
      new TextEncoder().encode(
        `data: ${JSON.stringify({
          ...current,
          type: "state",
          sequence: 2,
          execution_state: "stopped",
          current_action: null,
          recent_actions: [
            { action_id: "one", kind: "click", status: "cancelled" },
          ],
        })}\n\n`,
      ),
    );
    await flushPromises();
    expect(view.attributes("data-connection")).toBe("stopped");
    expect(view.find('[data-action-status="cancelled"]').exists()).toBe(true);
    expect(view.find('[data-action-status="running"]').exists()).toBe(false);
    expect(view.find("img").exists()).toBe(true);
    view.unmount();
  });
  it("recovers a half-open stream after missing heartbeats", async () => {
    vi.useFakeTimers();
    get
      .mockResolvedValueOnce(ok(current))
      .mockResolvedValueOnce(ok({ ...current, sequence: 1, image: "stale" }));
    const view = mount(AgentBrowserView, {
      props: { chatId: "chat-1", visible: true },
    });
    await flushPromises();
    await vi.advanceTimersByTimeAsync(15000);
    expect(view.attributes("data-connection")).toBe("disconnected");
    expect(view.find("img").exists()).toBe(false);
    get.mockResolvedValueOnce(ok({ available: false }));
    await vi.advanceTimersByTimeAsync(1000);
    expect(view.attributes("data-connection")).toBe("idle");
    view.unmount();
  });

  it("reconnects from a fresh snapshot and rejects replayed or regressing actions", async () => {
    vi.useFakeTimers();
    const action = { action_id: "one", kind: "click", status: "succeeded" };
    const frame = {
      ...current,
      sequence: 10,
      action_cursor: 8,
      image: "first",
      mime_type: "image/jpeg",
      width: 1280,
      height: 720,
      recent_actions: [action],
    };
    get.mockResolvedValueOnce(ok(current)).mockResolvedValueOnce(ok(frame));
    const view = mount(AgentBrowserView, {
      props: { chatId: "chat-1", visible: true },
    });
    await flushPromises();
    const signal = fetchStream.mock.calls[0][1].signal;
    stream.close();
    await flushPromises();
    expect(view.attributes("data-connection")).toBe("disconnected");
    expect(view.find("img").exists()).toBe(false);
    get
      .mockResolvedValueOnce(ok(current))
      .mockResolvedValueOnce(ok({ ...frame, sequence: 20, action_cursor: 18 }));
    await vi.advanceTimersByTimeAsync(1000);
    await flushPromises();
    expect(signal.aborted).toBe(true);
    expect(fetchStream.mock.calls[1][0]).toContain("action_cursor=18");
    for (const event of [
      {
        ...frame,
        type: "action",
        sequence: 19,
        current_action: { ...action, status: "running" },
      },
      {
        ...frame,
        type: "frame",
        sequence: 21,
        action_cursor: 8,
        current_action: { ...action, status: "running" },
        recent_actions: [],
      },
      {
        ...frame,
        type: "state",
        sequence: 22,
        action_cursor: 22,
        action_reset: true,
        recent_actions: [action],
      },
    ]) {
      stream.enqueue(
        new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`),
      );
      await flushPromises();
    }
    expect(view.findAll('[data-action-id="one"]')).toHaveLength(1);
    expect(view.find('[data-action-status="running"]').exists()).toBe(false);
    expect(view.text()).toContain("已重新同步");
    expect(view.attributes("data-sequence")).toBe("22");
    view.unmount();
    await vi.advanceTimersByTimeAsync(20000);
    expect(get).toHaveBeenCalledTimes(4);
  });

  it("resynchronizes a new runtime generation without applying the old stream", async () => {
    vi.useFakeTimers();
    get
      .mockResolvedValueOnce(ok(current))
      .mockResolvedValueOnce(
        ok({ ...current, sequence: 99, action_cursor: 90, image: "old" }),
      );
    const view = mount(AgentBrowserView, {
      props: { chatId: "chat-1", visible: true },
    });
    await flushPromises();
    const previousStream = stream;
    previousStream.close();
    await flushPromises();
    const next = {
      ...current,
      generation: "run-2",
      sequence: 1,
      action_cursor: 0,
      image: "new",
    };
    get.mockResolvedValueOnce(ok(next)).mockResolvedValueOnce(ok(next));
    await vi.advanceTimersByTimeAsync(1000);
    await flushPromises();
    expect(view.attributes("data-generation")).toBe("run-2");
    expect(view.attributes("data-sequence")).toBe("1");
    expect(view.text()).toContain("已重新同步");
    expect(fetchStream.mock.calls[1][0]).toContain("action_cursor=0");
    view.unmount();
  });

  it("cancels a pending reader on chat switch and never displays its late frame", async () => {
    get
      .mockResolvedValueOnce(ok(current))
      .mockResolvedValueOnce(ok({ ...current, sequence: 1, image: "private" }));
    const view = mount(AgentBrowserView, {
      props: { chatId: "chat-1", visible: true },
    });
    await flushPromises();
    const old = stream;
    get.mockResolvedValueOnce(ok({ available: false }));
    await view.setProps({ chatId: "chat-2" });
    expect(fetchStream.mock.calls[0][1].signal.aborted).toBe(true);
    expect(() =>
      old.enqueue(new TextEncoder().encode("data: {}\n\n")),
    ).toThrow();
    expect(view.find("img").exists()).toBe(false);
    expect(view.findAll("[data-action-id]")).toHaveLength(0);
    view.unmount();
  });

  it.each([
    [503, "disabled", "disabled"],
    [503, "runtime_unavailable", "unavailable"],
    [409, "busy", "busy"],
    [403, null, "denied"],
  ])("distinguishes HTTP state %s/%s", async (status, reason, expected) => {
    get.mockRejectedValueOnce({
      response: { status, data: { data: { reason } } },
    });
    const view = mount(AgentBrowserView, {
      props: { chatId: "chat-1", visible: true },
    });
    await flushPromises();
    expect(view.attributes("data-connection")).toBe(expected);
    expect(view.find("img").exists()).toBe(false);
    view.unmount();
  });

  it("polls an idle browser without starting it and cancels polling while hidden", async () => {
    vi.useFakeTimers();
    get.mockResolvedValue(ok({ available: false }));
    const view = mount(AgentBrowserView, {
      props: { chatId: "chat-1", visible: true },
    });
    await flushPromises();
    expect(view.attributes("data-connection")).toBe("idle");
    await vi.advanceTimersByTimeAsync(3000);
    expect(get).toHaveBeenCalledTimes(2);
    expect(
      get.mock.calls.every(([path]) => path === "browser-use/status"),
    ).toBe(true);
    expect(fetchStream).not.toHaveBeenCalled();
    await view.setProps({ visible: false });
    await vi.advanceTimersByTimeAsync(20000);
    expect(get).toHaveBeenCalledTimes(2);
    view.unmount();
  });

  it.each([
    { scroll_y: 500 },
    { scroll_x: 100 },
    { viewport_width: 1024 },
    { viewport_height: 600 },
  ])(
    "waits for matching pixels after an action changes coordinate space: %o",
    async (change) => {
      const page = {
        ...current,
        sequence: 1,
        url: "/form",
        document_id: "doc",
        viewport_width: 1280,
        viewport_height: 720,
        scroll_x: 0,
        scroll_y: 0,
        image: "OLD_SCROLL_PIXELS",
        mime_type: "image/jpeg",
        width: 1280,
        height: 720,
      };
      get.mockResolvedValueOnce(ok(current)).mockResolvedValueOnce(ok(page));
      const view = mount(AgentBrowserView, {
        props: { chatId: "chat-1", visible: true },
      });
      await flushPromises();
      const emit = async (event: unknown) => {
        stream.enqueue(
          new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`),
        );
        await flushPromises();
      };
      const moved = { ...page, ...change };
      const action = {
        action_id: "scrolled-input",
        kind: "type",
        status: "succeeded",
      };
      const highlight = {
        ...moved,
        action_id: action.action_id,
        x: 640,
        y: 360,
        expires_at: Date.now() / 1000 + 2,
      };
      await emit({
        ...moved,
        image: undefined,
        type: "action",
        sequence: 2,
        current_action: null,
        recent_actions: [action],
        highlight,
      });
      expect(view.find("img").exists()).toBe(false);
      expect(view.find(".agent-browser-target").exists()).toBe(false);
      // A late old-space frame must not acquire the new-space marker.
      await emit({
        ...page,
        type: "frame",
        sequence: 3,
        recent_actions: [action],
        highlight,
      });
      expect(view.find(".agent-browser-target").exists()).toBe(false);
      await emit({
        ...moved,
        image: "MATCHING_PIXELS",
        type: "frame",
        sequence: 4,
        recent_actions: [action],
        highlight,
      });
      expect(view.get("img").attributes("src")).toContain("MATCHING_PIXELS");
      expect(view.find(".agent-browser-target").exists()).toBe(true);
      view.unmount();
    },
  );

  it("renders safe action states and scales an actual marker, clearing it on reload and cancellation", async () => {
    const page = {
      ...current,
      sequence: 1,
      url: "/form",
      document_id: "doc-1",
      viewport_width: 1280,
      viewport_height: 720,
      scroll_x: 0,
      scroll_y: 0,
      image: "frame",
      mime_type: "image/jpeg",
      width: 1280,
      height: 720,
      action_cursor: 1,
    };
    get.mockResolvedValueOnce(ok(current)).mockResolvedValueOnce(ok(page));
    const view = mount(AgentBrowserView, {
      props: { chatId: "chat-1", visible: true },
    });
    await flushPromises();
    expect(fetchStream.mock.calls[0][0]).toContain("action_cursor=1");
    const action = { action_id: "actual", kind: "type", status: "running" };
    const emit = async (event: unknown) => {
      stream.enqueue(
        new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`),
      );
      await flushPromises();
    };
    await emit({
      ...page,
      type: "action",
      image: undefined,
      sequence: 2,
      current_action: action,
      recent_actions: [],
    });
    expect(view.find('[data-action-status="running"]').exists()).toBe(true);
    expect(view.find("img").exists()).toBe(true);
    const terminal = {
      ...page,
      type: "action",
      image: undefined,
      sequence: 3,
      current_action: null,
      recent_actions: [{ ...action, status: "succeeded" }],
      highlight: {
        ...page,
        action_id: "actual",
        x: 640,
        y: 360,
        expires_at: Date.now() / 1000 + 2,
      },
    };
    await emit(terminal);
    expect(view.get(".agent-browser-target").attributes("style")).toContain(
      "left: 50%",
    );
    expect(view.get(".agent-browser-target").attributes("style")).toContain(
      "top: 50%",
    );
    expect(view.find('[data-action-status="succeeded"]').exists()).toBe(true);
    await emit({ ...terminal, sequence: 4, document_id: "doc-2" });
    expect(view.find(".agent-browser-target").exists()).toBe(false);
    expect(view.find("img").exists()).toBe(false);
    await emit({
      ...page,
      sequence: 5,
      type: "frame",
      recent_actions: [{ ...action, status: "cancelled" }],
    });
    expect(view.find('[data-action-status="cancelled"]').exists()).toBe(true);
    expect(view.find(".agent-browser-target").exists()).toBe(false);
    view.unmount();
  });

  it("renders the owned frame and passes server identities to the snapshot request", async () => {
    get.mockResolvedValueOnce(ok(current)).mockResolvedValueOnce(
      ok({
        ...current,
        title: "中文表单",
        url: "http://127.0.0.1/form",
        width: 1280,
        height: 720,
        image: "abc",
        mime_type: "image/jpeg",
      }),
    );
    const view = mount(AgentBrowserView, {
      props: { chatId: "chat-1", visible: true },
    });
    await flushPromises();
    expect(get.mock.calls[1][1].params).toEqual({
      chat_id: "chat-1",
      generation: "run-1",
      browser_session_id: "browser-1",
    });
    expect(view.find("img").attributes("src")).toBe(
      "data:image/jpeg;base64,abc",
    );
    expect(view.text()).toContain("中文表单");
    expect(view.text()).toContain("http://127.0.0.1/form");
    view.unmount();
  });

  it("clears a prior chat image immediately and ignores its late response", async () => {
    let resolve!: (value: unknown) => void;
    get.mockReturnValueOnce(
      new Promise((r) => {
        resolve = r;
      }),
    );
    const view = mount(AgentBrowserView, {
      props: { chatId: "chat-1", visible: true },
    });
    get.mockResolvedValueOnce(ok({ available: false }));
    await view.setProps({ chatId: "chat-2" });
    resolve(ok(current));
    await flushPromises();
    expect(view.find("img").exists()).toBe(false);
    expect(get).toHaveBeenCalledTimes(2);
    view.unmount();
  });

  it("does not fetch while hidden and surfaces busy without retaining an image", async () => {
    const view = mount(AgentBrowserView, {
      props: { chatId: "chat-2", visible: false },
    });
    expect(get).not.toHaveBeenCalled();
    get.mockRejectedValueOnce({
      response: { status: 409, data: { data: { reason: "busy" } } },
    });
    await view.setProps({ visible: true });
    await flushPromises();
    expect(view.attributes("data-connection")).toBe("busy");
    expect(view.find("img").exists()).toBe(false);
    view.unmount();
  });

  it("follows new targets, rejects old sequences/generations and clears old pixels", async () => {
    get.mockResolvedValueOnce(ok(current)).mockResolvedValueOnce(
      ok({
        ...current,
        sequence: 1,
        image: "first",
        mime_type: "image/jpeg",
        width: 1280,
        height: 720,
      }),
    );
    const view = mount(AgentBrowserView, {
      props: { chatId: "chat-1", visible: true },
    });
    await flushPromises();
    const emit = async (data: unknown) => {
      stream.enqueue(
        new TextEncoder().encode(`data: ${JSON.stringify(data)}\n\n`),
      );
      await flushPromises();
    };
    await emit({
      ...current,
      tab_id: "tab-2",
      type: "state",
      sequence: 3,
      url: "/second",
    });
    expect(view.find("img").exists()).toBe(false);
    await emit({
      ...current,
      type: "frame",
      sequence: 2,
      image: "old",
      mime_type: "image/jpeg",
      width: 1280,
      height: 720,
    });
    await emit({
      ...current,
      generation: "old-run",
      type: "frame",
      sequence: 99,
      image: "old",
      mime_type: "image/jpeg",
      width: 1280,
      height: 720,
    });
    expect(view.find("img").exists()).toBe(false);
    await emit({
      ...current,
      tab_id: "tab-2",
      type: "frame",
      sequence: 4,
      url: "/second",
      title: "New tab",
      image: "new",
      mime_type: "image/jpeg",
      width: 1280,
      height: 720,
    });
    expect(view.get("img").attributes("src")).toContain("base64,new");
    expect(view.attributes("data-tab-id")).toBe("tab-2");
    expect(view.text()).toContain("New tab");
    const signal = fetchStream.mock.calls[0][1].signal;
    await view.setProps({ visible: false });
    expect(signal.aborted).toBe(true);
    expect(view.find("img").exists()).toBe(false);
    view.unmount();
  });

  it("marks a disconnected stream and obtains a fresh snapshot when reopened", async () => {
    const frame = {
      ...current,
      sequence: 1,
      image: "first",
      mime_type: "image/jpeg",
      width: 1280,
      height: 720,
    };
    get.mockResolvedValueOnce(ok(current)).mockResolvedValueOnce(ok(frame));
    const view = mount(AgentBrowserView, {
      props: { chatId: "chat-1", visible: true },
    });
    await flushPromises();
    stream.close();
    await flushPromises();
    expect(view.attributes("data-connection")).toBe("disconnected");
    await view.setProps({ visible: false });
    get
      .mockResolvedValueOnce(ok(current))
      .mockResolvedValueOnce(ok({ ...frame, sequence: 8 }));
    await view.setProps({ visible: true });
    await flushPromises();
    expect(view.get("img").attributes("src")).toContain("base64,first");
    expect(view.find('[role="alert"]').exists()).toBe(false);
    const signal = fetchStream.mock.calls[1][1].signal;
    view.unmount();
    expect(signal.aborted).toBe(true);
  });
});
