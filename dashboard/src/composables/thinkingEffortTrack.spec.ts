import { describe, expect, it } from "vitest";
import { DEFAULT_THINKING_EFFORT_SLIDER } from "./thinkingEffortSlider";
import { buildThinkingEffortTrack } from "./thinkingEffortTrack";

const LEVELS = [
  { name: "关闭", value: "none" },
  { name: "极低", value: "minimal" },
  { name: "低", value: "low" },
  { name: "中", value: "medium" },
  { name: "高", value: "high" },
  { name: "极高", value: "xhigh" },
  { name: "最大", value: "max" },
];

describe("buildThinkingEffortTrack (levels)", () => {
  it("positions stops by array index and labels the active level", () => {
    const view = buildThinkingEffortTrack({
      mode: "levels",
      value: "high",
      levels: LEVELS,
      slider: DEFAULT_THINKING_EFFORT_SLIDER,
    });

    expect(view.value).toBe(4);
    expect(view.percent).toBeCloseTo((4 / 6) * 100);
    expect(view.stops).toHaveLength(7);
    expect(view.stops.map((stop) => stop.label)).toEqual([
      "关闭",
      "极低",
      "低",
      "中",
      "高",
      "极高",
      "最大",
    ]);
    expect(view.stops[4].active).toBe(true);
    expect(view.activeLabel).toBe("高");
    expect(view.positionLabel).toBe("5 / 7");
    expect(view.disabled).toBe(false);
  });

  it("falls back to the first stop when the stored value is unknown", () => {
    const view = buildThinkingEffortTrack({
      mode: "levels",
      value: "removed-level",
      levels: LEVELS,
      slider: DEFAULT_THINKING_EFFORT_SLIDER,
    });
    expect(view.value).toBe(0);
    expect(view.activeLabel).toBe("关闭");
    expect(view.stops[0].active).toBe(true);
  });

  it("centres and disables a single-stop track", () => {
    const view = buildThinkingEffortTrack({
      mode: "levels",
      value: "low",
      levels: [{ name: "低", value: "low" }],
      slider: DEFAULT_THINKING_EFFORT_SLIDER,
    });
    expect(view.percent).toBe(50);
    expect(view.disabled).toBe(true);
    expect(view.stops[0].percent).toBe(50);
  });

  it("returns an empty, disabled track for a preset with no levels", () => {
    const view = buildThinkingEffortTrack({
      mode: "levels",
      value: "",
      levels: [],
      slider: DEFAULT_THINKING_EFFORT_SLIDER,
    });
    expect(view.stops).toEqual([]);
    expect(view.disabled).toBe(true);
    expect(view.activeLabel).toBe("—");
    expect(view.positionLabel).toBe("0 / 0");
  });
});

describe("buildThinkingEffortTrack (slider)", () => {
  it("maps a free value onto the numeric track", () => {
    const view = buildThinkingEffortTrack({
      mode: "slider",
      value: "75",
      levels: LEVELS,
      slider: DEFAULT_THINKING_EFFORT_SLIDER,
    });

    expect(view.value).toBe(75);
    expect(view.percent).toBeCloseTo((74 / 99) * 100);
    expect(view.activeLabel).toBe("75");
    expect(view.activeSubLabel).toBe("xhigh");
    expect(view.positionLabel).toBe("1–100");
    expect(view.stops.map((stop) => stop.value)).toEqual([
      "25",
      "50",
      "75",
      "100",
    ]);
  });

  it("resolves a stored alias to its numeric value", () => {
    const view = buildThinkingEffortTrack({
      mode: "slider",
      value: "high",
      levels: LEVELS,
      slider: DEFAULT_THINKING_EFFORT_SLIDER,
    });
    expect(view.value).toBe(50);
    expect(view.activeSubLabel).toBe("high");
  });

  it("clamps values to the configured range", () => {
    const high = buildThinkingEffortTrack({
      mode: "slider",
      value: "140",
      levels: LEVELS,
      slider: DEFAULT_THINKING_EFFORT_SLIDER,
    });
    const low = buildThinkingEffortTrack({
      mode: "slider",
      value: "-10",
      levels: LEVELS,
      slider: DEFAULT_THINKING_EFFORT_SLIDER,
    });
    expect(high.value).toBe(100);
    expect(high.percent).toBe(100);
    expect(low.value).toBe(1);
    expect(low.percent).toBe(0);
  });

  it("falls back to a usable grid for an inverted range", () => {
    const view = buildThinkingEffortTrack({
      mode: "slider",
      value: "50",
      levels: LEVELS,
      slider: { min: 90, max: 10, step: 0, snaps: [] },
    });
    expect(view.min).toBe(90);
    expect(view.max).toBe(91);
    expect(view.step).toBe(1);
    expect(view.value).toBe(90);
  });

  it("drops markers outside the range and keeps them sorted", () => {
    const view = buildThinkingEffortTrack({
      mode: "slider",
      value: "50",
      levels: LEVELS,
      slider: {
        min: 0,
        max: 100,
        step: 1,
        snaps: [
          { name: "max", value: 100 },
          { name: "outside", value: 120 },
          { name: "low", value: 10 },
          { name: "mid", value: 50 },
        ],
      },
    });
    expect(view.stops.map((stop) => stop.label)).toEqual([
      "low",
      "mid",
      "max",
    ]);
  });
});
