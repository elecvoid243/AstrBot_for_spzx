import { defineStore } from "pinia";

/** Summary shown on the app-bar goal entry button; null = no goal record. */
export interface GoalBadge {
  status: "active" | "paused" | "done";
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
    CLEAR_CONTEXT() {
      this.title = "";
      this.subtitle = "";
      this.projectId = "";
      this.workspaceFilesOpen = false;
      this.goalSidebarOpen = false;
      this.goalBadge = null;
    },
  },
});
