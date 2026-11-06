/** Captured pixels and their CSS viewport remain distinct from video dimensions. */
export interface BrowserFrame {
  available?: boolean;
  generation?: string;
  browser_session_id?: string;
  tab_id?: string;
  document_id?: string;
  url?: string;
  title?: string;
  viewport_width?: number;
  viewport_height?: number;
  scroll_x?: number;
  scroll_y?: number;
  position_revision?: number;
  control_epoch?: number;
  applied_input_id?: number;
  sequence?: number;
  coordinate_contract_version?: number;
  coordinate_ready?: boolean;
  coordinate_token?: string;
  frame_tree_token?: string;
  coordinate_event_revision?: number;
  frame_provenance_version?: number;
  frame_id?: string;
  media_session_id?: string;
  media_epoch?: number;
  rtp_timestamp?: number;
  media_sequence?: number;
  capture_sequence?: number;
  width?: number;
  height?: number;
  image?: string;
  mime_type?: string;
  capture_timestamp?: number;
}

export const browserTargetFields = [
  "tab_id",
  "document_id",
  "viewport_width",
  "viewport_height",
  "scroll_x",
  "scroll_y",
  "position_revision",
  "coordinate_token",
  "frame_id",
  "media_session_id",
  "media_epoch",
  "rtp_timestamp",
  "capture_sequence",
] as const;

export interface PresentedVideoFrame {
  rtpTimestamp?: number;
  presentedFrames: number;
  width: number;
  height: number;
}

/** Bound out-of-order metadata; only an actual presented RTP frame selects input. */
export class PresentedBrowserFrames {
  private records = new Map<number, BrowserFrame>();
  private latest: PresentedVideoFrame | null = null;
  private highestSequence = 0;
  private poisoned = false;
  private seen = new Map<number, string>();
  private latestAt = 0;
  private crossedEpoch = false;
  epoch = 0;

  constructor(
    readonly generation: string,
    readonly browserSessionId: string,
    readonly mediaSessionId: string,
  ) {}

  resetEpoch(epoch: number): void {
    if (!Number.isSafeInteger(epoch) || epoch < this.epoch) return;
    if (epoch > this.epoch) {
      this.records.clear();
      this.poisoned = false;
      this.crossedEpoch = this.epoch !== 0;
      this.epoch = epoch;
    }
  }

  add(record: BrowserFrame): boolean {
    if (
      record.available !== true ||
      record.generation !== this.generation ||
      record.browser_session_id !== this.browserSessionId ||
      record.media_session_id !== this.mediaSessionId ||
      record.coordinate_contract_version !== 2 ||
      !record.coordinate_ready ||
      !/^[a-f0-9]{32}$/.test(record.frame_id || "") ||
      !/^[a-f0-9]{64}$/.test(record.coordinate_token || "") ||
      !record.tab_id ||
      !record.document_id ||
      ![
        record.rtp_timestamp,
        record.media_epoch,
        record.media_sequence,
        record.capture_sequence,
        record.control_epoch,
        record.applied_input_id,
        record.position_revision,
      ].every(Number.isSafeInteger) ||
      record.control_epoch! < 0 ||
      record.applied_input_id! < 0 ||
      record.position_revision! < 0 ||
      record.capture_sequence! < 1 ||
      ![record.scroll_x, record.scroll_y].every(
        (value) => typeof value === "number" && Number.isFinite(value),
      ) ||
      record.rtp_timestamp! < 0 ||
      record.rtp_timestamp! > 0xffffffff ||
      record.media_epoch! < this.epoch ||
      record.media_epoch! < 1 ||
      record.media_sequence! <= Math.max(0, this.highestSequence - 128) ||
      ![
        record.width,
        record.height,
        record.viewport_width,
        record.viewport_height,
      ].every((value) => Number.isSafeInteger(value) && value! > 0) ||
      record.width! > 1920 ||
      record.height! > 1080
    )
      return false;
    this.resetEpoch(record.media_epoch!);
    if (this.poisoned) return false;
    const previous = this.seen.get(record.rtp_timestamp!);
    if (previous && previous !== record.frame_id) {
      this.records.clear();
      this.poisoned = true;
      return false;
    }
    if (
      [...this.records.values()].some(
        (value) =>
          value.media_sequence === record.media_sequence &&
          value.frame_id !== record.frame_id,
      )
    ) {
      this.records.clear();
      this.poisoned = true;
      return false;
    }
    this.records.set(record.rtp_timestamp!, Object.freeze({ ...record }));
    this.seen.set(record.rtp_timestamp!, record.frame_id!);
    while (this.seen.size > 128)
      this.seen.delete(this.seen.keys().next().value!);
    this.highestSequence = Math.max(
      this.highestSequence,
      record.media_sequence!,
    );
    while (this.records.size > 128) {
      const oldest = [...this.records.entries()].reduce((a, b) =>
        a[1].media_sequence! < b[1].media_sequence! ? a : b,
      );
      this.records.delete(oldest[0]);
    }
    return true;
  }

  present(frame: PresentedVideoFrame): BrowserFrame | null | undefined {
    if (this.latest && frame.presentedFrames <= this.latest.presentedFrames)
      return undefined;
    this.latest = { ...frame };
    this.latestAt = performance.now();
    this.crossedEpoch = false;
    return this.displayed();
  }

  displayed(): BrowserFrame | null {
    if (
      this.poisoned ||
      !this.latest ||
      !Number.isSafeInteger(this.latest.rtpTimestamp) ||
      // A delayed epoch can arrive after the new pixels, especially on a static
      // page. Keep that recent native presentation; never match a parked frame
      // from a previous 32-bit RTP clock cycle to freshly arrived metadata.
      (this.crossedEpoch && performance.now() - this.latestAt > 1000)
    )
      return null;
    const record = this.records.get(this.latest.rtpTimestamp!);
    if (
      record &&
      record.media_epoch === this.epoch &&
      record.width === this.latest.width &&
      record.height === this.latest.height
    ) {
      this.crossedEpoch = false;
      return record;
    }
    return null;
  }
}
