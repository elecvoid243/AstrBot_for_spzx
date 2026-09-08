// Author: askuserchoice_f1_impl
// Date: 2026-07-19
//
// Completeness test for the `interactiveChoice.cancelled` i18n key
// (ask_user_choice v1.2 dashboard work — Phase 2 / Task F1).
//
// The new "已取消" / "Cancelled" / "Отменено" key is consumed by the
// InteractiveChoiceBox state machine once a server-side
// `interactive_choice_resolved { reason: "cancelled" }` SSE event
// arrives (see Phase 1 plugin PR #1). This test pins that all three
// locale tables ship the key so the box never renders a
// "[MISSING: interactiveChoice.cancelled]" placeholder in any
// supported UI language.

import { describe, expect, it } from "vitest";
import chatZh from "./locales/zh-CN/features/chat.json";
import chatEn from "./locales/en-US/features/chat.json";
import chatRu from "./locales/ru-RU/features/chat.json";

const localizations: Array<[string, Record<string, unknown>]> = [
  ["zh-CN", chatZh as unknown as Record<string, unknown>],
  ["en-US", chatEn as unknown as Record<string, unknown>],
  ["ru-RU", chatRu as unknown as Record<string, unknown>],
];

describe("spcodeProjectLoad git-workflow history filter i18n completeness", () => {
  // 2026-07-30: the Git history panel's "应用/重置" buttons were
  // renamed to "筛选/重设筛选条件" in zh-CN. Pin the two key paths
  // across all three locales so a future accidental rename (or a
  // fix that only updates one file) cannot desync the button copy.
  for (const [locale, dict] of localizations) {
    it(`${locale} defines spcodeProjectLoad…history.filter.apply + .reset`, () => {
      const nodes = dict.spcodeProjectLoad as
        | Record<string, unknown>
        | undefined;
      const diffSidebar = nodes?.diffSidebar as
        | Record<string, unknown>
        | undefined;
      const gitWorkflow = diffSidebar?.gitWorkflow as
        | Record<string, unknown>
        | undefined;
      const history = gitWorkflow?.history as
        | Record<string, unknown>
        | undefined;
      const filter = history?.filter as
        | Record<string, unknown>
        | undefined;
      expect(
        typeof filter?.apply,
        `${locale} missing history.filter.apply string`,
      ).toBe("string");
      expect(
        typeof filter?.reset,
        `${locale} missing history.filter.reset string`,
      ).toBe("string");
    });
  }
});

describe("interactiveChoice i18n completeness", () => {
  for (const [locale, dict] of localizations) {
    it(`${locale} defines interactiveChoice.cancelled`, () => {
      const interactiveChoice = dict.interactiveChoice as
        | Record<string, unknown>
        | undefined;
      expect(
        interactiveChoice,
        `${locale} missing interactiveChoice block`,
      ).toBeDefined();
      expect(
        typeof interactiveChoice?.cancelled,
        `${locale} missing interactiveChoice.cancelled string`,
      ).toBe("string");
    });

    // 2026-07-23: cancel button on the pending box header needs
    // both the visible label and the aria-label. Pin both so the
    // button never falls back to the [MISSING: ...] placeholder.
    it(`${locale} defines interactiveChoice.cancel + cancelAria`, () => {
      const interactiveChoice = dict.interactiveChoice as
        | Record<string, unknown>
        | undefined;
      expect(
        typeof interactiveChoice?.cancel,
        `${locale} missing interactiveChoice.cancel string`,
      ).toBe("string");
      expect(
        typeof interactiveChoice?.cancelAria,
        `${locale} missing interactiveChoice.cancelAria string`,
      ).toBe("string");
    });
  }
});

describe("subagentCollapse i18n completeness", () => {
  // 2026-09-08: the sticky collapse button on the subagent run card uses
  // this key for both its title and aria-label. Pin it across the three
  // locales so the button never renders a "[MISSING: …]" placeholder.
  for (const [locale, dict] of localizations) {
    it(`${locale} defines subagentCollapse.hint`, () => {
      const subagentCollapse = dict.subagentCollapse as
        | Record<string, unknown>
        | undefined;
      expect(
        typeof subagentCollapse?.hint,
        `${locale} missing subagentCollapse.hint string`,
      ).toBe("string");
    });
  }
});
