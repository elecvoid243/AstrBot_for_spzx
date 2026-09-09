// Author: elecvoid243, 2026-07-19
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

function readSiblingSource(filename: string): string {
  return readFileSync(fileURLToPath(new URL(filename, import.meta.url)), "utf8");
}

describe("TodoSummaryBar and GitDiffSidebar layering contract", () => {
  it("lowers the TodoSummaryBar below the Git Diff fullscreen layer", () => {
    const chatSource = readSiblingSource("./Chat.vue");
    const gitDiffSource = readSiblingSource("./GitDiffSidebar.vue");

    expect(gitDiffSource).toContain(
      '(e: "fullscreen-change", v: boolean): void',
    );
    expect(gitDiffSource).toContain('emit("fullscreen-change", v);');
    expect(chatSource).toContain("const gitDiffFullscreen = ref(false);");
    expect(chatSource).toMatch(
      /@fullscreen-change="gitDiffFullscreen = \$event"/,
    );
    expect(chatSource).toMatch(
      /'todo-summary-bar--gitdiff-fullscreen':\s*\n?\s*gitDiffSidebarOpen\s*&&\s*gitDiffFullscreen/,
    );
    // Base layer: above the sidebar fullscreen layer (1300) but below
    // Vuetify v-dialog (default zIndex 2400, +10 per stacked overlay), so an
    // open dialog's scrim covers the bar and makes it non-clickable.
    expect(chatSource).toMatch(
      /\.todo-summary-bar\s*\{[\s\S]*?z-index:\s*1400;/,
    );
    expect(chatSource).toMatch(
      /\.todo-summary-bar--gitdiff-fullscreen\s*\{[\s\S]*?z-index:\s*1200;/,
    );
    expect(gitDiffSource).toMatch(
      /\.git-diff-sidebar\.is-fullscreen\s*\{[\s\S]*?z-index:\s*1300;/,
    );
  });

  // 2026-09-09: the expandable floating menu replaced TodoSidebar. It is a
  // position:fixed sibling of the pill and must obey the same layering
  // contract: below the pill (so the pill stays clickable) and below the
  // Git Diff fullscreen layer when that is active.
  it("keeps the expandable todo menu below the pill and the gitdiff fullscreen layer", () => {
    const chatSource = readSiblingSource("./Chat.vue");

    expect(chatSource).toMatch(/\.todo-summary-menu\s*\{[\s\S]*?z-index:\s*1390;/);
    expect(chatSource).toMatch(
      /\.todo-summary-menu--gitdiff-fullscreen\s*\{[\s\S]*?z-index:\s*1190;/,
    );
  });
});
