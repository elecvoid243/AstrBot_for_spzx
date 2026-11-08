// Source contract for the admin scheduled-task shortcut (author: elecvoid243,
// 2026-11-08). The entry lives in the ChatUI sidebar under the model-config
// and new-chat rows, admin-only, and opens the cron page in a dialog so
// configuring a job never unloads the running conversation.
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const chatSource = readFileSync(resolve("src/components/chat/Chat.vue"), "utf8");
const headerSource = readFileSync(
  resolve("src/layouts/full/vertical-header/VerticalHeader.vue"),
  "utf8",
);

describe("scheduled-task entry in the ChatUI sidebar", () => {
  it("sits below new chat and above the batch/search/import row", () => {
    expect(chatSource).toMatch(
      /tm\("actions\.newChat"\)[\s\S]*?class="new-chat-btn sidebar-cron-btn/,
    );
    expect(chatSource).toMatch(
      /class="new-chat-btn sidebar-cron-btn[\s\S]*?class="sidebar-top-actions"/,
    );
  });

  it("is admin-only, including the collapsed rail form", () => {
    expect(chatSource).toMatch(
      /v-if="!isOrdinaryUser && isSidebarCollapsed"[\s\S]*?class="new-chat-btn sidebar-cron-btn/,
    );
    expect(chatSource).toMatch(
      /v-else-if="!isOrdinaryUser"[\s\S]*?class="new-chat-btn sidebar-cron-btn"/,
    );
  });

  it("opens the cron page in a dialog instead of leaving the conversation", () => {
    expect(chatSource).toMatch(
      /<v-dialog v-model="scheduledTasksOpen"[\s\S]*?<CronJobPage[\s\S]*?embedded/,
    );
    expect(chatSource).not.toContain('"_blank"');
    expect(chatSource).toMatch(
      /const CronJobPage = defineAsyncComponent\(/,
    );
  });

  it("reuses the navigation label and keeps the app bar free of a duplicate", () => {
    expect(chatSource).toContain('t("core.navigation.cron")');
    expect(headerSource).not.toContain("cron-shortcut-trigger");
  });
});
