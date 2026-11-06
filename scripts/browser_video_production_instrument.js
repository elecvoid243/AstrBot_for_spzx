// Acceptance-only observation/fault injection; never imported by production UI.
(() => {
  const proof = window.__videoProof = {
    peers: [], sockets: [], metadata: [], frames: [], inputs: [], commands: [], acks: [], protocol: [],
    heldInputs: [], heldMetadata: [], holdInput: false, holdMetadata: false,
    caseId: null, mode: "video", attached: false,
  };
  const now = () => performance.timeOrigin + performance.now();
  proof.statistics = async () => Promise.all(proof.peers.map(async (peer, index) => {
    let stats = [];
    try { stats = Array.from((await peer.getStats()).values()); } catch { /* Closed peer. */ }
    return { index, state: peer.connectionState, rtp: stats.filter(s => s.type === 'inbound-rtp' && s.kind === 'video').map(s => ({
      bytesReceived:s.bytesReceived, packetsReceived:s.packetsReceived, packetsLost:s.packetsLost,
      framesDecoded:s.framesDecoded, framesDropped:s.framesDropped,keyFramesDecoded:s.keyFramesDecoded,
      jitter:s.jitter,totalDecodeTime:s.totalDecodeTime,
    })) };
  }));
  const NativePeer = window.RTCPeerConnection;
  window.RTCPeerConnection = class extends NativePeer {
    constructor(config) { super(config); proof.peers.push(this); }
  };
  const NativeSocket = window.WebSocket;
  window.WebSocket = class extends NativeSocket {
    constructor(...args) {
      super(...args);
      proof.sockets.push(this);
      this.addEventListener("message", event => {
        const record = JSON.parse(event.data);
        if (this.url.includes("browser-media")) {
          if (record.type !== 'frame') proof.protocol.push({type:record.type,version:record.frame_metadata_version,reason:record.reason,epoch:record.media_epoch});
          if (proof.holdMetadata && ["frame", "epoch"].includes(record.type)) {
            if (proof.heldMetadata.length >= 128) throw new Error("Metadata fault budget exceeded");
            proof.heldMetadata.push([this, event.data]);
            event.stopImmediatePropagation();
            return;
          }
          if (record.type === "frame") proof.metadata.push({ ...record, received: now() });
        } else if (this.url.includes("browser-control")) {
          proof.acks.push({ ...record, received: now() });
        }
      });
    }
    send(data) {
      if (this.url.includes("browser-control")) {
        const command = JSON.parse(data);
        if (command.input_id) {
          proof.commands.push({ ...command, sent: now(), caseId: proof.caseId, presentedRtp: proof.frames.at(-1)?.rtp });
          if (proof.holdInput && ["click", "wheel", "pointer"].includes(command.operation)) {
            proof.heldInputs.push([this, data]);
            return;
          }
        }
      }
      return super.send(data);
    }
  };
  proof.flushInput = () => {
    proof.holdInput = false;
    for (const [socket, data] of proof.heldInputs.splice(0)) NativeSocket.prototype.send.call(socket, data);
  };
  proof.flushMetadata = () => {
    proof.holdMetadata = false;
    for (const [socket, data] of proof.heldMetadata.splice(0)) socket.dispatchEvent(new MessageEvent("message", { data }));
  };
  for (const type of ["pointerdown", "wheel", "keydown", "beforeinput"]) {
    document.addEventListener(type, event => {
      if (proof.caseId && event.target.closest(".agent-browser-viewport"))
        proof.inputs.push({ type, caseId: proof.caseId, t: now(), frameId: document.querySelector('.agent-browser-view')?.dataset.frameId });
    }, true);
  }
  proof.attach = () => {
    if (proof.attached) return;
    proof.attached = true;
    const video = document.querySelector(".agent-browser-viewport video");
    const canvas = document.createElement("canvas");
    canvas.width = 1152; canvas.height = 70;
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    const sample = (source, metadata = {}) => {
      const sourceWidth = source.videoWidth || source.naturalWidth;
      if (!sourceWidth) return;
      const scale = sourceWidth / 1280;
      ctx.drawImage(source, 0, 0, 576 * scale, 50 * scale, 0, 0, 576, 50);
      ctx.drawImage(source, 600 * scale, 0, 576 * scale, 70 * scale, 576, 0, 576, 70);
      const pixels = ctx.getImageData(0, 0, 1152, 70).data;
      const groups = [];
      for (let group = 0; group < 2; group++) {
        const words = [];
        for (let row = 0; row < (group ? 7 : 5); row++) {
          let word = 0;
          for (let bit = 0; bit < 32; bit++)
            if (pixels[((row * 10 + 5) * 1152 + group * 576 + bit * 18 + 9) * 4] > 128) word += 2 ** bit;
          words.push(word);
        }
        groups.push(words);
      }
      proof.frames.push({
        t: now() + Math.max(0, (metadata.expectedDisplayTime || 0) - (metadata.callbackTime || 0)),
        words: groups[0], actual: groups[1], mode: proof.mode,
        valid: groups[0][4] === 0x41535452 && groups[1][0] === 0x41535452,
        rtp: metadata.rtpTimestamp, width: sourceWidth, height: source.videoHeight || source.naturalHeight,
      });
    };
    const presented = (time, metadata) => {
      if (proof.mode === "video") sample(video, { ...metadata, callbackTime: time });
      video.requestVideoFrameCallback(presented);
    };
    video.requestVideoFrameCallback(presented);
    const image = document.querySelector(".agent-browser-viewport img:last-of-type");
    image.addEventListener("load", () => {
      if (proof.mode === "jpeg") requestAnimationFrame(() => requestAnimationFrame(() => sample(image)));
    });
  };
})();
