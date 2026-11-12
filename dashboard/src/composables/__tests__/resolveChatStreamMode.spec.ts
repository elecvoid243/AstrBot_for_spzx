import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { resolveChatStreamMode } from "../useMessages";

beforeEach(() => {
  localStorage.clear();
  window.history.replaceState({}, "", "/");
});

afterEach(() => {
  localStorage.clear();
  window.history.replaceState({}, "", "/");
});

describe("resolveChatStreamMode", () => {
  it("defaults to legacy under the test runner", () => {
    expect(resolveChatStreamMode()).toBe("legacy");
  });

  it("reads the stored preference", () => {
    localStorage.setItem("chat.streamMode", "unified");
    expect(resolveChatStreamMode()).toBe("unified");

    localStorage.setItem("chat.streamMode", "legacy");
    expect(resolveChatStreamMode()).toBe("legacy");
  });

  it("lets the query param override the stored preference", () => {
    localStorage.setItem("chat.streamMode", "unified");
    expect(resolveChatStreamMode("?stream=legacy")).toBe("legacy");

    localStorage.setItem("chat.streamMode", "legacy");
    expect(resolveChatStreamMode("?stream=unified")).toBe("unified");
  });

  it("ignores unknown values and falls back to the default", () => {
    localStorage.setItem("chat.streamMode", "carrier-pigeon");
    expect(resolveChatStreamMode("?stream=nope")).toBe("legacy");
  });
});
