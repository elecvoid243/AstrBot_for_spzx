// Author: elecvoid243, 2026-10-03
import { describe, expect, it } from "vitest";
import {
  DEFAULT_THINKING_EFFORT_SLIDER,
  effortValueLabel,
  findEffortSnap,
  normalizeEffortSliderConfig,
  normalizeEffortValue,
  validateEffortSliderConfig,
} from "./thinkingEffortSlider";

describe("normalizeEffortValue", () => {
  it("leaves a dragged value exactly where the user put it", () => {
    // No magnetic pulling towards the aliases: 23 / 48 / 99 stay free values.
    expect(normalizeEffortValue(23, DEFAULT_THINKING_EFFORT_SLIDER)).toBe(23);
    expect(normalizeEffortValue(48, DEFAULT_THINKING_EFFORT_SLIDER)).toBe(48);
    expect(normalizeEffortValue(99, DEFAULT_THINKING_EFFORT_SLIDER)).toBe(99);
    expect(normalizeEffortValue(37, DEFAULT_THINKING_EFFORT_SLIDER)).toBe(37);
  });

  it("clamps out-of-range drags to the track bounds", () => {
    expect(normalizeEffortValue(0, DEFAULT_THINKING_EFFORT_SLIDER)).toBe(1);
    expect(normalizeEffortValue(140, DEFAULT_THINKING_EFFORT_SLIDER)).toBe(100);
  });

  it("clamps values onto a custom track", () => {
    const config = { min: 0, max: 10, step: 2, snaps: [] };
    expect(normalizeEffortValue(-3, config)).toBe(0);
    expect(normalizeEffortValue(4, config)).toBe(4);
    expect(normalizeEffortValue(12, config)).toBe(10);
    // Landing on an alias is coincidence, not magnetism.
    expect(
      normalizeEffortValue(4, { ...config, snaps: [{ name: "l", value: 4 }] }),
    ).toBe(4);
  });
});

describe("findEffortSnap / effortValueLabel", () => {
  it("resolves the alias a value sits on", () => {
    expect(findEffortSnap(50, DEFAULT_THINKING_EFFORT_SLIDER)).toEqual({
      name: "high",
      value: 50,
    });
    expect(findEffortSnap(37, DEFAULT_THINKING_EFFORT_SLIDER)).toBeNull();
  });

  it("labels snapped values with the alias and free values with the bare number", () => {
    expect(effortValueLabel(50, DEFAULT_THINKING_EFFORT_SLIDER)).toBe("high 50");
    expect(effortValueLabel(37, DEFAULT_THINKING_EFFORT_SLIDER)).toBe("37");
  });
});

describe("validateEffortSliderConfig", () => {
  it("accepts the shipped defaults", () => {
    expect(validateEffortSliderConfig(DEFAULT_THINKING_EFFORT_SLIDER)).toBeNull();
  });

  it("reports each invalid field", () => {
    const base = DEFAULT_THINKING_EFFORT_SLIDER;
    expect(validateEffortSliderConfig({ ...base, max: base.min })).toBe("range");
    expect(validateEffortSliderConfig({ ...base, min: Number.NaN })).toBe("range");
    expect(validateEffortSliderConfig({ ...base, step: 0 })).toBe("step");
    expect(
      validateEffortSliderConfig({ ...base, snaps: [{ name: " ", value: 25 }] }),
    ).toBe("snapName");
    expect(
      validateEffortSliderConfig({
        ...base,
        snaps: [{ name: "low", value: Number.NaN }],
      }),
    ).toBe("snapValue");
    expect(
      validateEffortSliderConfig({ ...base, snaps: [{ name: "low", value: 200 }] }),
    ).toBe("snapRange");
    expect(
      validateEffortSliderConfig({
        ...base,
        snaps: [
          { name: "a", value: 25 },
          { name: "b", value: 25 },
        ],
      }),
    ).toBe("snapDuplicate");
  });
});

describe("normalizeEffortSliderConfig", () => {
  it("falls back to the defaults for junk input", () => {
    expect(normalizeEffortSliderConfig(null)).toEqual(DEFAULT_THINKING_EFFORT_SLIDER);
    expect(normalizeEffortSliderConfig("nope")).toEqual(
      DEFAULT_THINKING_EFFORT_SLIDER,
    );
  });

  it("merges partial stored config over the defaults", () => {
    const merged = normalizeEffortSliderConfig({ max: 64, snaps: [{ name: "l", value: 16 }] });
    expect(merged).toEqual({
      min: 1,
      max: 64,
      step: 1,
      snaps: [{ name: "l", value: 16 }],
    });
  });

  it("drops malformed snaps and reverts an inverted range", () => {
    const merged = normalizeEffortSliderConfig({
      min: 90,
      max: 10,
      step: 2,
      snaps: [{ name: "ok", value: 20 }, { name: "", value: 30 }, "junk", { name: "x", value: "y" }],
    });
    // The inverted range falls back to the default track; the surviving snap
    // keeps the user's intent instead of being clamped away.
    expect(merged.min).toBe(DEFAULT_THINKING_EFFORT_SLIDER.min);
    expect(merged.max).toBe(DEFAULT_THINKING_EFFORT_SLIDER.max);
    expect(merged.step).toBe(2);
    expect(merged.snaps).toEqual([{ name: "ok", value: 20 }]);
  });
});
