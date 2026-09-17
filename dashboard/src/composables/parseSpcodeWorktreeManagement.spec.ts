// parseSpcodeWorktreeManagement.spec.ts
//
// Regression + guard tests for the worktree-management reason classification
// (2026-09-17, elecvoid243).
//
// Why this exists:
//   A typo in the "start point" field of the worktree-create dialog makes git
//   fail with `fatal: invalid reference: <ref>`. The plugin maps that stderr to
//   the reason code `cannot_checkout_missing` (tools/webapi/git_worktree_add.py,
//   _map_add_stderr_to_reason) — but the frontend whitelist here did not know
//   that code, so it fell back to `unknown`, whose i18n key
//   (`error.reason.unknown`) was missing from every locale. The user saw
//   "MISSING: features.chat.spcodeProjectLoad.diffSidebar.error.reason.unknown".
//
//   Two guards below keep both halves honest:
//     1. every reason the backend can emit must classify to a real key;
//     2. every key the code can render must exist in the shipped locales.
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  ALLOWED_WORKTREE_REASONS,
  WORKTREE_MGMT_REASON_CODES,
  classifyWorktreeReason,
} from "./parseSpcodeWorktreeManagement";

type Endpoint = "add" | "remove" | "lock" | "unlock" | "activate";

// Backend-reachable reason codes per endpoint, taken from the plugin sources:
// literal `reason="..."` values, the stderr mapping tables in
// git_worktree_{add,remove,lock,unlock}.py, and what `_git_endpoint_preflight`
// can inject (feature_disabled / no_project_loaded / worktree_invalid /
// directory_missing / not_a_git_repo / git_unavailable / path_unsafe).
const BACKEND_REACHABLE: Record<Endpoint, string[]> = {
  add: [
    "feature_disabled",
    "no_project_loaded",
    "worktree_invalid",
    "directory_missing",
    "not_a_git_repo",
    "git_unavailable",
    "git_error",
    "invalid_body",
    "invalid_branch",
    "invalid_param",
    "path_unsafe",
    "path_exists_nonempty",
    "cannot_create_existing",
    "cannot_checkout_missing",
    "worktree_not_in_repo",
  ],
  remove: [
    "feature_disabled",
    "no_project_loaded",
    "worktree_invalid",
    "directory_missing",
    "not_a_git_repo",
    "git_unavailable",
    "git_error",
    "invalid_body",
    "path_unsafe",
    "worktree_not_found",
    "cannot_remove_main",
    "worktree_locked",
    "worktree_dirty",
  ],
  lock: [
    "feature_disabled",
    "no_project_loaded",
    "worktree_invalid",
    "directory_missing",
    "not_a_git_repo",
    "git_unavailable",
    "git_error",
    "invalid_body",
    "path_unsafe",
    "worktree_not_found",
    "already_locked",
  ],
  unlock: [
    "feature_disabled",
    "no_project_loaded",
    "worktree_invalid",
    "directory_missing",
    "not_a_git_repo",
    "git_unavailable",
    "git_error",
    "invalid_body",
    "path_unsafe",
    "worktree_not_found",
    "not_locked",
  ],
  activate: [
    "feature_disabled",
    "no_project_loaded",
    "worktree_invalid",
    "directory_missing",
    "not_a_git_repo",
    "git_unavailable",
    "git_error",
    "invalid_body",
    "path_unsafe",
    "worktree_not_found",
  ],
};

describe("worktree reason classification", () => {
  it("classifies cannot_checkout_missing (bad start point) instead of unknown", () => {
    const meta = classifyWorktreeReason("cannot_checkout_missing", "add");
    expect(meta.i18nKey).toBe("error.reason.cannot_checkout_missing");
  });

  it("classifies worktree_not_in_repo instead of unknown", () => {
    const meta = classifyWorktreeReason("worktree_not_in_repo", "add");
    expect(meta.i18nKey).toBe("error.reason.worktree_not_in_repo");
  });

  it.each(Object.entries(BACKEND_REACHABLE))(
    "maps every backend-reachable reason for the %s endpoint",
    (endpoint, reasons) => {
      const unknownKeys = reasons
        .filter(
          (r) =>
            classifyWorktreeReason(r, endpoint as Endpoint).i18nKey ===
            "error.reason.unknown",
        )
        .map((r) => `${endpoint}:${r}`);
      expect(unknownKeys).toEqual([]);
    },
  );

  it("keeps the whitelist and the classification table in sync", () => {
    for (const [endpoint, reasons] of Object.entries(ALLOWED_WORKTREE_REASONS)) {
      const missing = reasons.filter((r) => !(r in WORKTREE_MGMT_REASON_CODES));
      expect({ endpoint, missing }).toEqual({ endpoint, missing: [] });
    }
  });

  it("still falls back to unknown for a genuinely unknown code", () => {
    expect(classifyWorktreeReason("some_future_code", "add").i18nKey).toBe(
      "error.reason.unknown",
    );
    expect(classifyWorktreeReason(null, "add").i18nKey).toBe(
      "error.reason.unknown",
    );
  });
});

describe("worktree reason i18n completeness", () => {
  // Every key the code can render (the classification table + the `generic`
  // fallback used by the sidebar's own error block).
  const required = [
    ...new Set([
      ...Object.values(WORKTREE_MGMT_REASON_CODES).map((m) => m.i18nKey),
      "error.reason.generic",
    ]),
  ].map((k) => k.replace("error.reason.", ""));

  // ja-JP is a partial locale (its spcodeProjectLoad block is intentionally
  // absent and falls back), so only the complete locales are asserted here.
  const locales = ["zh-CN", "en-US", "ru-RU"];
  const here = path.dirname(fileURLToPath(import.meta.url));

  it.each(locales)(
    "locale %s defines every reason key the code can render",
    (locale) => {
      const file = path.resolve(
        here,
        `../i18n/locales/${locale}/features/chat.json`,
      );
      const json = JSON.parse(readFileSync(file, "utf-8")) as {
        spcodeProjectLoad?: {
          diffSidebar?: { error?: { reason?: Record<string, string> } };
        };
      };
      const node = json.spcodeProjectLoad?.diffSidebar?.error?.reason ?? {};
      const missing = required.filter((key) => !(key in node));
      expect({ locale, missing }).toEqual({ locale, missing: [] });
    },
  );
});
