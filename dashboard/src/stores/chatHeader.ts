import { defineStore } from "pinia";
import type { ShellSessionListItem } from "@/components/chat/message_list_comps/shell_session_tools/format";
import type { TodoSnapshot } from "@/composables/useMessages";

/** Summary shown on the app-bar goal entry button; null = no goal record. */
export interface GoalBadge {
  status: "active" | "paused" | "blocked" | "done";
  turnsUsed: number;
  maxTurns: number;
}

export const useChatHeaderStore = defineStore("chatHeader", {
  state: () => ({
    title: "",
    sessionId: "",
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
    // 2026-11-07 (elecvoid243): the todo summary moved out of the message
    // panel into the app bar, so both the snapshot and the menu's open state
    // have to be reachable from VerticalHeader. Same push-based pattern as
    // goalBadge: Chat.vue owns the per-session snapshot (useMessages parses
    // it from the todo tools) and mirrors the active session's copy here.
    todoSnapshot: null as TodoSnapshot | null,
    todoMenuOpen: false,
    // 2026-10-05: managed shell session indicator. Same push pattern as the
    // goal badge: Chat.vue owns the per-session cache (useShellSessions) and
    // mirrors the active session's list here so the VerticalHeader button
    // can badge and list it. null = no sessions (indicator hidden).
    shellSessions: null as ShellSessionListItem[] | null,
    // 2026-10-05: floating shell-session output windows. Open order IS the
    // stacking order — the last entry renders topmost; FOCUS moves an entry
    // to the end. Reopening an existing id raises instead of duplicating.
    openShellWindows: [] as string[],
    shellWindowSize: { width: 560, height: 420 },
  }),

  actions: {
    SET_CONTEXT(payload: {
      title?: string;
      sessionId?: string;
      subtitle?: string;
      projectId?: string;
    }) {
      const nextProjectId = payload.projectId || "";
      if (this.projectId !== nextProjectId) {
        this.workspaceFilesOpen = false;
      }
      this.title = payload.title || "";
      this.sessionId = payload.sessionId || "";
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
    SET_TODO_SNAPSHOT(snapshot: TodoSnapshot | null) {
      this.todoSnapshot = snapshot;
      // A cleared list must not leave an open menu showing an empty state.
      if (!snapshot) this.todoMenuOpen = false;
    },
    SET_TODO_MENU_OPEN(open: boolean) {
      this.todoMenuOpen = Boolean(open && this.todoSnapshot);
    },
    TOGGLE_TODO_MENU() {
      this.SET_TODO_MENU_OPEN(!this.todoMenuOpen);
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
      this.sessionId = "";
      this.subtitle = "";
      this.projectId = "";
      this.workspaceFilesOpen = false;
      this.goalSidebarOpen = false;
      this.goalBadge = null;
      this.todoSnapshot = null;
      this.todoMenuOpen = false;
      this.shellSessions = null;
      this.openShellWindows = [];
    },
  },
});
