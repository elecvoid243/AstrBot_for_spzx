// Author: elecvoid243
// Date: 2026-10-08
//
// Lifecycle tests for the composer's compression chip. The chip is the only
// part of the notice the user sees, so its timing is a contract: it must show
// up for the run that compressed, survive a duplicate delivery of the same
// event without restarting its own timer, restart when a later compression
// changes the numbers, and vanish the moment the session is left.

import { effectScope, nextTick, ref } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ContextCompressionEntry } from "./contextCompressionNotice";
import { useContextCompressionChip } from "./useContextCompressionChip";

const translate = (key: string, params?: Record<string, string | number>) =>
  key.startsWith("contextCompression.strategy.")
    ? key
    : `${key}|${params?.from}|${params?.to}`;

const ENTRY: ContextCompressionEntry = {
  key: "s1:123456:45123",
  notice: {
    strategy: "llm_compress",
    tokensBefore: 123456,
    tokensAfter: 45123,
  },
};

function setup() {
  const source = ref<ContextCompressionEntry | null>(null);
  const scope = effectScope();
  const api = scope.run(
    () => useContextCompressionChip(() => source.value, translate),
  )!;
  return { source, scope, ...api };
}

describe("useContextCompressionChip", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders nothing while no compression happened", () => {
    const { chip, visible, historyLine, scope } = setup();

    expect(visible.value).toBe(false);
    expect(chip.value).toBeNull();
    expect(historyLine.value).toBeNull();
    scope.stop();
  });

  it("shows the chip for 6s and keeps the ring line afterwards", async () => {
    const { source, chip, visible, historyLine, scope } = setup();

    source.value = ENTRY;
    await nextTick();

    expect(visible.value).toBe(true);
    expect(chip.value?.label).toBe("123.5k → 45.1k");
    expect(chip.value?.description).toBe(
      "contextCompression.llm|123.5k|45.1k",
    );

    vi.advanceTimersByTime(5999);
    expect(visible.value).toBe(true);

    vi.advanceTimersByTime(1);
    expect(visible.value).toBe(false);
    // The numbers stay reachable through the ring tooltip — that is the
    // whole point of the second line.
    expect(historyLine.value).toBe(
      "contextCompression.lastCompression|123.5k|45.1k",
    );

    scope.stop();
  });

  it("ignores a replay of the same event instead of extending its own timer", async () => {
    const { source, visible, scope } = setup();

    source.value = ENTRY;
    await nextTick();
    vi.advanceTimersByTime(3000);

    source.value = { ...ENTRY };
    await nextTick();
    vi.advanceTimersByTime(2999);
    expect(visible.value).toBe(true);

    vi.advanceTimersByTime(1);
    expect(visible.value).toBe(false);

    scope.stop();
  });

  it("restarts with the new numbers when a later compression arrives", async () => {
    const { source, chip, visible, scope } = setup();

    source.value = ENTRY;
    await nextTick();
    vi.advanceTimersByTime(5000);

    source.value = {
      key: "s1:45123:9000",
      notice: { strategy: "truncate_by_turns", tokensBefore: 45123, tokensAfter: 9000 },
    };
    await nextTick();

    expect(chip.value?.icon).toBe("mdi-content-cut");
    expect(chip.value?.lossy).toBe(true);
    vi.advanceTimersByTime(5999);
    expect(visible.value).toBe(true);
    vi.advanceTimersByTime(1);
    expect(visible.value).toBe(false);

    scope.stop();
  });

  it("hides immediately when the session is left", async () => {
    const { source, chip, visible, scope } = setup();

    source.value = ENTRY;
    await nextTick();
    expect(visible.value).toBe(true);

    source.value = null;
    await nextTick();

    expect(visible.value).toBe(false);
    expect(chip.value).toBeNull();

    // The pending timer must be gone: a later tick cannot resurrect the chip.
    vi.advanceTimersByTime(10000);
    expect(visible.value).toBe(false);

    scope.stop();
  });

  it("stops its timer with the owning scope", async () => {
    const { source, scope } = setup();

    source.value = ENTRY;
    await nextTick();
    scope.stop();

    expect(() => vi.advanceTimersByTime(10000)).not.toThrow();
  });
});
