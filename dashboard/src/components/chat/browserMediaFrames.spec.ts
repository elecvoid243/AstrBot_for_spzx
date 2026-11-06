import { describe, expect, it } from "vitest";
import {
  PresentedBrowserFrames,
  type BrowserFrame,
} from "./browserMediaFrames";

const frame: BrowserFrame = {
  available: true,
  generation: "run",
  browser_session_id: "browser",
  media_session_id: "m".repeat(32),
  frame_id: "a".repeat(32),
  tab_id: "tab",
  document_id: "doc",
  coordinate_contract_version: 2,
  coordinate_ready: true,
  coordinate_token: "b".repeat(64),
  control_epoch: 2,
  applied_input_id: 4,
  position_revision: 0,
  viewport_width: 1280,
  viewport_height: 900,
  scroll_x: 0,
  scroll_y: 120,
  width: 1536,
  height: 1080,
  media_epoch: 1,
  media_sequence: 1,
  rtp_timestamp: 0xfffffff0,
  capture_sequence: 1,
};
const presented = {
  rtpTimestamp: frame.rtp_timestamp,
  width: 1536,
  height: 1080,
  presentedFrames: 1,
};

describe("actual presented browser frame provenance", () => {
  it("waits for matching late metadata without borrowing a newer state", () => {
    const frames = new PresentedBrowserFrames(
      "run",
      "browser",
      frame.media_session_id!,
    );
    expect(frames.present(presented)).toBeNull();
    expect(frames.add({ ...frame, rtp_timestamp: 42, media_sequence: 2 })).toBe(
      true,
    );
    expect(frames.displayed()).toBeNull();
    expect(frames.add(frame)).toBe(true);
    expect(frames.displayed()).toEqual(frame);
    expect(frames.displayed()).not.toBe(frame);
  });

  it("uses actual encoded dimensions, not the CSS viewport or snapshot size", () => {
    const frames = new PresentedBrowserFrames(
      "run",
      "browser",
      frame.media_session_id!,
    );
    frames.add(frame);
    expect(
      frames.present({ ...presented, width: 1280, height: 900 }),
    ).toBeNull();
    expect(frames.present({ ...presented, presentedFrames: 2 })).toEqual(frame);
    expect(
      frames.present({ ...presented, width: 100, presentedFrames: 1 }),
    ).toBeUndefined();
    expect(frames.displayed()).toEqual(frame);
  });

  it("orders by media sequence across unsigned RTP wrap and bounded late records", () => {
    const frames = new PresentedBrowserFrames(
      "run",
      "browser",
      frame.media_session_id!,
    );
    frames.add(frame);
    const wrapped = {
      ...frame,
      frame_id: "c".repeat(32),
      rtp_timestamp: 12,
      media_sequence: 2,
    };
    frames.add(wrapped);
    expect(frames.present({ ...presented, rtpTimestamp: 12 })).toEqual(wrapped);
    frames.add(frame);
    expect(frames.displayed()).toEqual(wrapped);
    for (let sequence = 3; sequence < 140; sequence++) {
      frames.add({
        ...frame,
        rtp_timestamp: sequence * 3000,
        media_sequence: sequence,
        frame_id: sequence.toString(16).padStart(32, "0"),
      });
    }
    expect(frames.add(frame)).toBe(false);
    expect(frames.displayed()).toBeNull();
  });

  it.each(["rtp_timestamp", "media_sequence"] as const)(
    "freezes ambiguous %s reuse until a new epoch",
    (field) => {
      const frames = new PresentedBrowserFrames(
        "run",
        "browser",
        frame.media_session_id!,
      );
      frames.add(frame);
      frames.present(presented);
      frames.add({
        ...frame,
        frame_id: "c".repeat(32),
        rtp_timestamp: 44,
        media_sequence: 2,
        [field]: frame[field],
      });
      expect(frames.displayed()).toBeNull();
      expect(frames.add(frame)).toBe(false);
      const fresh = {
        ...frame,
        media_epoch: 2,
        media_sequence: 3,
        rtp_timestamp: 99,
        frame_id: "d".repeat(32),
      };
      expect(frames.add(fresh)).toBe(true);
      expect(frames.displayed()).toBeNull();
      expect(
        frames.present({ ...presented, rtpTimestamp: 99, presentedFrames: 2 }),
      ).toEqual(fresh);
      expect(frames.add(frame)).toBe(false);
    },
  );

  it("recovers static new pixels presented before their delayed epoch and metadata", () => {
    const frames = new PresentedBrowserFrames(
      "run",
      "browser",
      frame.media_session_id!,
    );
    frames.add(frame);
    frames.present(presented);
    expect(
      frames.present({ ...presented, rtpTimestamp: 44, presentedFrames: 2 }),
    ).toBeNull();
    frames.resetEpoch(2);
    const fresh = {
      ...frame,
      frame_id: "e".repeat(32),
      media_epoch: 2,
      media_sequence: 2,
      rtp_timestamp: 44,
    };
    expect(frames.add(fresh)).toBe(true);
    expect(frames.displayed()).toEqual(fresh);
    frames.resetEpoch(1);
    expect(frames.displayed()).toEqual(fresh);
  });

  it.each([
    { generation: "other" },
    { browser_session_id: "other" },
    { media_session_id: "other" },
    { coordinate_token: undefined },
    { coordinate_ready: false },
    { frame_id: undefined },
    { available: false },
    { tab_id: "" },
    { document_id: undefined },
    { applied_input_id: undefined },
    { control_epoch: -1 },
    { capture_sequence: 0 },
    { scroll_x: NaN },
    { rtp_timestamp: 0x100000000 },
    { width: 1536.5 },
  ])("rejects incomplete, unknown or foreign metadata: %j", (patch) => {
    const frames = new PresentedBrowserFrames(
      "run",
      "browser",
      frame.media_session_id!,
    );
    expect(frames.add({ ...frame, ...patch })).toBe(false);
    expect(frames.present(presented)).toBeNull();
  });
});
