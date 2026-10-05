import { defineStore } from "pinia";
import type { ShellSessionListItem } from "@/components/chat/message_list_comps/shell_session_tools/format";

/** Summary shown on the app-bar goal entry button; null = no goal record. */
export interface GoalBadge {
  status: "active" | "paused" | "blocked" | "done";
  turnsUsed: number;
  maxTurns: number;
}

export const useChatHeaderStore = defineStore("chatHeader", {
  state: () => ({
    title: "",
    subtitle: "",
    projectId: "",
    workspaceFilesOpen: false,
    // 2026-09-09: persistent GoalSidebar entry. The goal state lives in the
    // kernel KV (per-UMO) and only reaches the dashboard via the
    // GET /chat/sessions/{id}/goal endpoint, so the open state + a compact
    // turns badge live here where the VerticalHeader app-bar button can
    // reach them. Chat.vue owns the badge content (it fetches and caches
    // the goal state per session) and syncs it in.
    goalSidebarOpen: false,
    goalBadge: null as GoalBadge | null,
    // 2026-10-05: managed shell session indicator. Same push pattern as the
    // goal badge: Chat.vue owns the per-session cache (useShellSessions) and
    // mirrors the active session's list here so the VerticalHeader button
    // can badge and list it. null = no sessions (indicator hidden).
    shellSessions: null as ShellSessionListItem[] | null,
    // 2026-10-05: floating shell-session output windows. Open order IS the
    // stacking order — the last entry renders topmost; FOCUS moves an entry
    // to the end. Reopening an existing id raises instead of duplicating.
    openShellWindows: [] as string[],
  }),

  actions: {
    SET_CONTEXT(payload: {
      title?: string;
      subtitle?: string;
      projectId?: string;
    }) {
      const nextProjectId = payload.projectId || "";
      if (this.projectId !== nextProjectId) {
        this.workspaceFilesOpen = false;
      }
      this.title = payload.title || "";
      this.subtitle = payload.subtitle || "";
      this.projectId = nextProjectId;
    },
    TOGGLE_WORKSPACE_FILES() {
      if (this.projectId) {
        this.workspaceFilesOpen = !this.workspaceFilesOpen;
      }
    },
    SET_WORKSPACE_FILES_OPEN(open: boolean) {
      this.workspaceFilesOpen = Boolean(open && this.projectId);
    },
    TOGGLE_GOAL_SIDEBAR() {
      this.goalSidebarOpen = !this.goalSidebarOpen;
    },
    SET_GOAL_SIDEBAR_OPEN(open: boolean) {
      this.goalSidebarOpen = Boolean(open);
    },
    SET_GOAL_BADGE(badge: GoalBadge | null) {
      this.goalBadge = badge;
    },
    SET_SHELL_SESSIONS(sessions: ShellSessionListItem[] | null) {
      this.shellSessions = sessions;
    },
    SET_SHELL_WINDOW_OPEN(sessionId: string, open: boolean) {
      if (open) {
        this.FOCUS_SHELL_WINDOW(sessionId);
      } else {
        this.openShellWindows = this.openShellWindows.filter(
          (id) => id !== sessionId,
        );
      }
    },
    FOCUS_SHELL_WINDOW(sessionId: string) {
      this.openShellWindows = [
        ...this.openShellWindows.filter((id) => id !== sessionId),
        sessionId,
      ];
    },
    CLEAR_CONTEXT() {
      this.title = "";
      this.subtitle = "";
      this.projectId = "";
      this.workspaceFilesOpen = false;
      this.goalSidebarOpen = false;
      this.goalBadge = null;
      this.shellSessions = null;
      this.openShellWindows = [];
    },
  },
});
