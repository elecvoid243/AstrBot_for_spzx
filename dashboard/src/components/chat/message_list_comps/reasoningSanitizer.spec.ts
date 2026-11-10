import { describe, expect, it } from "vitest";
import {
  isSanitizerMarkerLine,
  parseThinkSegments,
} from "./reasoningSanitizer";

describe("parseThinkSegments", () => {
  it("splits a period marker from the surrounding reasoning", () => {
    const text =
      "before\n[context sanitizer: collapsed 7 x 5-line repeated block]\nafter";

    expect(parseThinkSegments(text)).toEqual([
      { kind: "text", text: "before" },
      { kind: "period", repeats: 7, period: 5 },
      { kind: "text", text: "after" },
    ]);
  });

  it("parses the run and fallback variants", () => {
    const text = [
      "a",
      "[context sanitizer: collapsed 8 x repeated line]",
      "b",
      "[context sanitizer: collapsed repetitive reasoning (1741 lines, 28231 chars)]",
      "c",
    ].join("\n");

    expect(parseThinkSegments(text)).toEqual([
      { kind: "run", repeats: 8 },
      { kind: "text", text: "b" },
      { kind: "fallback", lines: 1741, chars: 28231 },
      { kind: "text", text: "c" },
    ]);
  });

  it("hides the kept first occurrence before a period marker", () => {
    const text =
      "intro\nblock 1\nblock 2\nblock 3\n[context sanitizer: collapsed 2 x 3-line repeated block]\nafter";

    expect(parseThinkSegments(text)).toEqual([
      { kind: "text", text: "intro" },
      { kind: "period", repeats: 2, period: 3 },
      { kind: "text", text: "after" },
    ]);
  });

  it("hides the kept line before a run marker", () => {
    const text =
      "intro\nkeep\n[context sanitizer: collapsed 8 x repeated line]\nafter";

    expect(parseThinkSegments(text)).toEqual([
      { kind: "text", text: "intro" },
      { kind: "run", repeats: 8 },
      { kind: "text", text: "after" },
    ]);
  });

  it("keeps ordinary text as one segment", () => {
    const text = "first line\nsecond line";

    expect(parseThinkSegments(text)).toEqual([
      { kind: "text", text: "first line\nsecond line" },
    ]);
  });

  it("does not treat an inline mention as a marker", () => {
    const text =
      "The model wrote [context sanitizer: collapsed 7 x 5-line repeated block] inline.";

    expect(parseThinkSegments(text)).toEqual([{ kind: "text", text }]);
  });

  it("preserves indentation inside a text segment", () => {
    const text =
      "```c\n  int value = 1;\n```\n[context sanitizer: collapsed 7 x 5-line repeated block]\nafter";

    expect(parseThinkSegments(text)[0]).toEqual({
      kind: "text",
      text: "```c\n  int value = 1;\n```",
    });
  });
});

describe("isSanitizerMarkerLine", () => {
  it("recognizes all known marker variants", () => {
    expect(
      isSanitizerMarkerLine(
        "[context sanitizer: collapsed 7 x 5-line repeated block]",
      ),
    ).toBe(true);
    expect(
      isSanitizerMarkerLine("[context sanitizer: collapsed 8 x repeated line]"),
    ).toBe(true);
    expect(
      isSanitizerMarkerLine(
        "[context sanitizer: collapsed repetitive reasoning (10 lines, 512 chars)]",
      ),
    ).toBe(true);
  });

  it("rejects ordinary reasoning lines", () => {
    expect(isSanitizerMarkerLine("Let me go.")).toBe(false);
    expect(
      isSanitizerMarkerLine(
        "prefix [context sanitizer: collapsed 7 x 5-line repeated block]",
      ),
    ).toBe(false);
  });
});
