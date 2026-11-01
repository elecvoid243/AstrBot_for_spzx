// Author: elecvoid243
// Date: 2026-09-14
//
// Tracks chat sessions the user manually marked as unread via the sidebar
// context menu, so they stay highlighted with the same calm green marker
// as the run-finished attention. In-memory like the other attention
// stores: opening the session reads it and drops the mark.

import { defineStore } from "pinia";
import { accountSessionRevision } from "@/utils/accountSession";

export const useSessionUnreadStore = defineStore("sessionUnread", {
  state: () => ({
    // Bare conversation session ids marked unread by the user.
    sessionIds: [] as string[],
    accountRevision: accountSessionRevision.value,
  }),
  getters: {
    count: (state) =>
      state.accountRevision === accountSessionRevision.value
        ? state.sessionIds.length
        : 0,
  },
  actions: {
    isUnread(sessionId: string): boolean {
      if (this.accountRevision !== accountSessionRevision.value) {
        this.sessionIds = [];
        this.accountRevision = accountSessionRevision.value;
      }
      return this.sessionIds.includes(sessionId);
    },
    markUnread(sessionId: string): void {
      this.isUnread("");
      if (!sessionId || this.sessionIds.includes(sessionId)) return;
      this.sessionIds.push(sessionId);
    },
    markRead(sessionId: string): void {
      this.isUnread("");
      this.sessionIds = this.sessionIds.filter((id) => id !== sessionId);
    },
  },
});
