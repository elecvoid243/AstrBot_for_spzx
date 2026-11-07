// Source contract for the composer goal bar (author: elecvoid243, 2026-11-07).
//
// The goal moved out of a right-hand drawer into a strip above the composer.
// These assertions keep the split honest: the chat page owns the data and the
// slot wiring, the bar owns the three direct actions, and the old drawer does
// not creep back.
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

function readSource(filename: string): string {
  return readFileSync(fileURLToPath(new URL(filename, import.meta.url)), "utf8");
}

describe("composer goal bar", () => {
  const chatSource = readSource("./Chat.vue");
  const barSource = readSource("./GoalBar.vue");
  const inputSource = readSource("./ChatInput.vue");
  const storeSource = readSource("../../stores/chatHeader.ts");

  it("renders inside the composer slot instead of the old drawer", () => {
    expect(chatSource).toMatch(/<template #above-status>/);
    expect(chatSource).toMatch(/<GoalBar[\s\S]*?:goal="currentGoal"/);
    expect(chatSource).not.toContain("GoalSidebar");
    expect(inputSource).toMatch(/<slot name="above-status" \/>/);
  });

  it("exposes pause, edit and clear directly on the bar", () => {
    expect(barSource).toMatch(/@click="act\('pause'\)"/);
    expect(barSource).toMatch(/@click="act\('resume'\)"/);
    expect(barSource).toMatch(/@click="act\('clear'\)"/);
    expect(barSource).toMatch(/@click="openEdit"/);
    expect(barSource).toMatch(/chatApi\.setSessionGoal/);
  });

  it("shares the edit dialog through the header store", () => {
    expect(storeSource).toMatch(/goalEditOpen:\s*false/);
    expect(storeSource).toMatch(/SET_GOAL_EDIT_OPEN\(open: boolean\)/);
    expect(barSource).toMatch(/get: \(\) => chatHeader\.goalEditOpen/);
  });
});
