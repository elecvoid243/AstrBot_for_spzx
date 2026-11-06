import { ref, type Ref } from "vue";
import { browserMediaApi } from "@/api/v1";
import {
  PresentedBrowserFrames,
  type BrowserFrame,
} from "./browserMediaFrames";

/** One explicit media attempt; capture epochs do not reconnect its peer/socket. */
export function useAgentBrowserVideo(
  video: Ref<HTMLVideoElement | null>,
  onPresented: (frame: BrowserFrame | null) => void,
  onRevoked: () => void,
) {
  const phase = ref<
    "idle" | "starting" | "live" | "frozen" | "failed" | "unsupported"
  >("idle");
  const visible = ref(false);
  const lastFrame = ref<BrowserFrame | null>(null);
  const frozenPreview = ref<string | null>(null);
  let peer: RTCPeerConnection | null = null;
  let socket: WebSocket | null = null;
  let frames: PresentedBrowserFrames | null = null;
  let attempt = 0;
  let callback: number | null = null;
  let cancellation: AbortController | null = null;
  let lastPixels: VideoFrame | null = null;
  let heartbeat: ReturnType<typeof setInterval> | undefined;
  let startup: ReturnType<typeof setTimeout> | undefined;

  function stop(clear = true) {
    attempt++;
    cancellation?.abort();
    cancellation = null;
    clearInterval(heartbeat);
    clearTimeout(startup);
    if (callback !== null) video.value?.cancelVideoFrameCallback(callback);
    callback = null;
    const oldSocket = socket;
    socket = null;
    oldSocket?.close();
    peer?.close();
    peer = null;
    frames = null;
    lastPixels?.close();
    lastPixels = null;
    video.value?.pause();
    onPresented(null);
    if (clear) {
      if (video.value) video.value.srcObject = null;
      visible.value = false;
      lastFrame.value = null;
      frozenPreview.value = null;
    }
    phase.value = "idle";
  }

  function freeze() {
    if (lastPixels) {
      try {
        const canvas = document.createElement("canvas");
        canvas.width = lastPixels.displayWidth;
        canvas.height = lastPixels.displayHeight;
        canvas.getContext("2d")?.drawImage(lastPixels, 0, 0);
        frozenPreview.value = canvas.toDataURL("image/jpeg", 0.95);
      } catch {
        /* Retain the paused native frame if canvas is unavailable. */
      }
    }
  }

  function fail(unsupported = false) {
    freeze();
    stop(false);
    phase.value = unsupported ? "unsupported" : "failed";
  }

  async function start(identity: {
    chat_id: string;
    generation: string;
    browser_session_id: string;
  }) {
    freeze();
    stop(!visible.value);
    const revision = attempt;
    const scope = new AbortController();
    cancellation = scope;
    if (
      !video.value ||
      typeof RTCPeerConnection === "undefined" ||
      typeof VideoFrame === "undefined" ||
      typeof video.value.requestVideoFrameCallback !== "function"
    ) {
      phase.value = "unsupported";
      return;
    }
    phase.value = "starting";
    const current = new RTCPeerConnection({ iceServers: [] });
    peer = current;
    current.addTransceiver("video", { direction: "recvonly" });
    const rendered = () => {
      if (attempt !== revision || !frames) return;
      const record = frames.displayed();
      if (!record && visible.value && !frozenPreview.value) freeze();
      onPresented(record);
      if (record) {
        clearTimeout(startup);
        if (record.frame_id !== lastFrame.value?.frame_id && video.value) {
          try {
            const pixels = new VideoFrame(video.value);
            lastPixels?.close();
            lastPixels = pixels;
          } catch {
            fail();
            return;
          }
        }
        lastFrame.value = record;
        frozenPreview.value = null;
        visible.value = true;
        phase.value = "live";
      } else if (visible.value) phase.value = "frozen";
    };
    current.ontrack = (event) => {
      const element = video.value;
      if (!element || attempt !== revision) return;
      element.srcObject = event.streams[0] || new MediaStream([event.track]);
      const presented: VideoFrameRequestCallback = (_time, metadata) => {
        if (attempt !== revision || !video.value) return;
        const rtp = (
          metadata as VideoFrameCallbackMetadata & { rtpTimestamp?: number }
        ).rtpTimestamp;
        if (!Number.isSafeInteger(rtp)) {
          fail(!visible.value);
          return;
        }
        const record = frames?.present({
          rtpTimestamp: rtp,
          presentedFrames: metadata.presentedFrames,
          width: metadata.width,
          height: metadata.height,
        });
        if (record !== undefined) rendered();
        if (attempt === revision)
          callback = video.value.requestVideoFrameCallback(presented);
      };
      callback = element.requestVideoFrameCallback(presented);
      void element.play().catch(() => {
        if (attempt === revision) fail();
      });
    };
    current.onconnectionstatechange = () => {
      if (
        attempt === revision &&
        ["failed", "disconnected"].includes(current.connectionState)
      )
        fail();
    };
    startup = setTimeout(() => {
      if (attempt === revision) fail();
    }, 12000);
    try {
      const response = await browserMediaApi.createSession(identity);
      if (attempt !== revision) return;
      const session = response.data;
      const url = new URL(session.websocket_path, location.href);
      url.protocol = location.protocol === "https:" ? "wss:" : "ws:";
      const channel = new WebSocket(url);
      socket = channel;
      channel.onmessage = (event) => {
        if (attempt !== revision) return;
        try {
          const data = JSON.parse(event.data);
          if (data.type === "answer") {
            if (
              data.frame_metadata_version !== 1 ||
              !/^[a-f0-9]{32}$/.test(data.media_session_id || "")
            ) {
              fail(true);
              return;
            }
            frames = new PresentedBrowserFrames(
              identity.generation,
              identity.browser_session_id,
              data.media_session_id,
            );
            void current
              .setRemoteDescription({ type: "answer", sdp: data.sdp })
              .catch(() => {
                if (attempt === revision) fail();
              });
            heartbeat = setInterval(() => {
              if (channel.readyState === WebSocket.OPEN)
                channel.send(JSON.stringify({ operation: "heartbeat" }));
            }, 5000);
          } else if (data.type === "frame") {
            if (frames?.add(data)) rendered();
          } else if (data.type === "epoch") {
            if (!frames || data.media_epoch <= frames.epoch) return;
            freeze();
            frames?.resetEpoch(data.media_epoch);
            onPresented(null);
            if (visible.value) phase.value = "frozen";
          } else if (data.type === "revoked") {
            stop();
            onRevoked();
          } else if (data.type === "media-error") fail();
        } catch {
          fail();
        }
      };
      channel.onclose = (event) => {
        if (attempt !== revision) return;
        if (event.code === 1008) {
          stop();
          onRevoked();
        } else fail();
      };
      channel.onerror = () => {
        if (attempt === revision) fail();
      };
      await new Promise<void>((resolve, reject) => {
        channel.addEventListener("open", () => resolve(), {
          once: true,
          signal: scope.signal,
        });
        channel.addEventListener(
          "error",
          () => reject(new Error("Media socket unavailable")),
          { once: true, signal: scope.signal },
        );
        channel.addEventListener(
          "close",
          () => reject(new Error("Media socket closed")),
          { once: true, signal: scope.signal },
        );
        scope.signal.addEventListener(
          "abort",
          () => reject(new Error("Media attempt cancelled")),
          { once: true },
        );
      });
      if (attempt !== revision) return;
      await current.setLocalDescription(await current.createOffer());
      if (current.iceGatheringState !== "complete") {
        await new Promise<void>((resolve, reject) => {
          current.addEventListener(
            "icegatheringstatechange",
            () => {
              if (current.iceGatheringState === "complete") resolve();
            },
            { signal: scope.signal },
          );
          scope.signal.addEventListener(
            "abort",
            () => reject(new Error("Media attempt cancelled")),
            { once: true },
          );
        });
      }
      if (attempt === revision)
        channel.send(
          JSON.stringify({
            ticket: session.ticket,
            type: "offer",
            sdp: current.localDescription!.sdp,
          }),
        );
    } catch {
      if (attempt === revision) fail();
    }
  }

  return { phase, visible, lastFrame, frozenPreview, start, stop, freeze };
}
