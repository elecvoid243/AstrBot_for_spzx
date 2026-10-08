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

import {
  contextCompressionChip,
  contextCompressionHistoryLine,
} from "@/composables/contextCompressionNotice";
import { useModuleI18n } from "@/i18n/composables";
import chatZh from "./locales/zh-CN/features/chat.json";
import chatEn from "./locales/en-US/features/chat.json";
import chatJa from "./locales/ja-JP/features/chat.json";
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

describe("all-branches scope i18n completeness (2026-10-05)", () => {
  // 「所有分支」入口寄生在历史视图的分支选择器首项 —— 三语都缺不得,
  // 否则选择器会渲染出 [MISSING: ...] 占位。
  for (const [locale, dict] of localizations) {
    it(`${locale} defines spcodeProjectLoad…history.filter.allRefs`, () => {
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
      const filter = history?.filter as Record<string, unknown> | undefined;
      expect(
        typeof filter?.allRefs,
        `${locale} missing history.filter.allRefs string`,
      ).toBe("string");
    });
  }
});

describe("context compression chip i18n completeness (2026-10-08)", () => {
  // The chip itself carries numbers only, so the strategy has to be named in
  // words by the ring tooltip's "last compression" line. A locale missing one
  // of these renders "[MISSING: contextCompression.strategy.…]" in the tooltip
  // — ja-JP included, which the other blocks of this file do not cover.
  const allLocales: Array<[string, Record<string, unknown>]> = [
    ...localizations,
    ["ja-JP", chatJa as unknown as Record<string, unknown>],
  ];

  for (const [locale, dict] of allLocales) {
    it(`${locale} defines contextCompression.lastCompression + strategy words`, () => {
      const compression = dict.contextCompression as
        | Record<string, unknown>
        | undefined;
      expect(
        typeof compression?.lastCompression,
        `${locale} missing contextCompression.lastCompression string`,
      ).toBe("string");

      const strategy = compression?.strategy as
        | Record<string, unknown>
        | undefined;
      for (const key of ["llm", "truncate", "halving", "generic"]) {
        expect(
          typeof strategy?.[key],
          `${locale} missing contextCompression.strategy.${key} string`,
        ).toBe("string");
      }
    });
  }

  // Presence is not enough for the two keys with parameters: a renamed
  // placeholder ({strategy} → {kind}) interpolates to itself and ships a
  // literal "{strategy}" in the tooltip, which the leaf-module tests cannot
  // see because they pass a fake translate.
  it("substitutes every placeholder of the chip and the tooltip line", () => {
    const { tm } = useModuleI18n("features/chat");
    const notice = {
      strategy: "llm_compress",
      tokensBefore: 123456,
      tokensAfter: 45123,
    };

    expect(contextCompressionChip(notice, tm).description).toBe(
      "上下文已压缩 · 123.5k → 45.1k tokens",
    );
    expect(contextCompressionHistoryLine(notice, tm)).toBe(
      "上次压缩：123.5k → 45.1k tokens · 摘要",
    );
  });
});

describe("diff overlay mode slot i18n completeness (2026-10-08)", () => {
  // 第三格的 tooltip 有三态(在途 / 不可用原因)加进入退出两种动作文案。
  // ru-RU 的 diffSidebar.overlay 块此前完全缺失(tooltip 会渲染
  // "[MISSING: ...]"),这次一并补齐并钉死,避免只有 zh 有词条。
  const OVERLAY_KEYS = [
    "toOverlay",
    "toDiff",
    "loading",
    "disabledNoBase",
    "disabledError",
    "disabledTruncated",
  ];

  for (const [locale, dict] of localizations) {
    it(`${locale} defines every diffSidebar.overlay tooltip state`, () => {
      const projectLoad = dict.spcodeProjectLoad as
        | Record<string, unknown>
        | undefined;
      const diffSidebar = projectLoad?.diffSidebar as
        | Record<string, unknown>
        | undefined;
      const overlay = diffSidebar?.overlay as
        | Record<string, unknown>
        | undefined;

      for (const key of OVERLAY_KEYS) {
        expect(
          typeof overlay?.[key],
          `${locale} missing diffSidebar.overlay.${key} string`,
        ).toBe("string");
      }
    });
  }
});
