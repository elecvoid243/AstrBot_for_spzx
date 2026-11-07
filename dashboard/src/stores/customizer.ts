import { defineStore } from "pinia";
import config, { type ThemeMode, resolveUiTheme } from "@/config";

const DARK_THEMES: ReadonlySet<string> = new Set(["PurpleThemeDark"]);

// 2026-07-21 chatui sidebar resize (elecvoid243): persisted widths
// for the left chat sidebar. Read once at module load so the very
// first render uses the user's previous choice — no flash from the
// hard-coded default.
const CHAT_SIDEBAR_WIDTH_KEY = "chatui.chatSidebarWidth.v1";
const CHAT_SIDEBAR_DEFAULT_WIDTH = 280;
const CHAT_SIDEBAR_MIN_WIDTH = 200;
const CHAT_SIDEBAR_MAX_WIDTH = 480;

function clampChatSidebarWidth(value: number): number {
  if (!Number.isFinite(value)) return CHAT_SIDEBAR_DEFAULT_WIDTH;
  return Math.min(
    CHAT_SIDEBAR_MAX_WIDTH,
    Math.max(CHAT_SIDEBAR_MIN_WIDTH, Math.round(value)),
  );
}

function loadChatSidebarWidth(): number {
  if (typeof localStorage === "undefined") return CHAT_SIDEBAR_DEFAULT_WIDTH;
  const raw = localStorage.getItem(CHAT_SIDEBAR_WIDTH_KEY);
  if (raw === null) return CHAT_SIDEBAR_DEFAULT_WIDTH;
  const parsed = Number.parseInt(raw, 10);
  return clampChatSidebarWidth(parsed);
}

export const useCustomizerStore = defineStore("customizer", {
  state: () => ({
    Sidebar_drawer: config.Sidebar_drawer,
    Customizer_drawer: config.Customizer_drawer,
    mini_sidebar: config.mini_sidebar,
    fontTheme: "Noto Sans SC",
    uiTheme: config.uiTheme,
    themeMode: config.themeMode as ThemeMode,
    inputBg: config.inputBg,
    chatSidebarOpen: false, // chat mode mobile sidebar state
    chatSidebarCollapsed: false, // chat mode desktop sidebar state
    // 2026-07-21 chatui sidebar resize (elecvoid243): user-resizable
    // width for the desktop left sidebar. Persisted in localStorage so
    // it survives reloads and is shared between /chat and /chatbox.
    chatSidebarWidth: loadChatSidebarWidth(),
    // 2026-11-07 (elecvoid243): true while the sidebar splitter is being
    // dragged. The app bar reads it to drop Vuetify's `.v-toolbar` left/width
    // transition for the gesture — that 200ms ease made the header visibly
    // chase the sidebar. A gesture flag, so it is never persisted.
    chatSidebarResizing: false,
  }),

  getters: {
    isDark: (state) => (state.uiTheme ? DARK_THEMES.has(state.uiTheme) : false),
  },

  actions: {
    SET_SIDEBAR_DRAWER() {
      this.Sidebar_drawer = !this.Sidebar_drawer;
    },
    SET_MINI_SIDEBAR(payload: boolean) {
      this.mini_sidebar = payload;
    },
    SET_FONT(payload: string) {
      this.fontTheme = payload;
    },

    SET_UI_THEME(payload: string) {
      this.uiTheme = payload;
      localStorage.setItem("uiTheme", payload);
      const mode: ThemeMode = payload === "PurpleThemeDark" ? "dark" : "light";
      this.themeMode = mode;
      localStorage.setItem("themeMode", mode);
    },

    SET_THEME_MODE(mode: ThemeMode) {
      this.themeMode = mode;
      localStorage.setItem("themeMode", mode);
      const uiTheme = resolveUiTheme(mode);
      this.uiTheme = uiTheme;
      localStorage.setItem("uiTheme", uiTheme);
    },

    TOGGLE_CHAT_SIDEBAR() {
      this.chatSidebarOpen = !this.chatSidebarOpen;
    },
    SET_CHAT_SIDEBAR(payload: boolean) {
      this.chatSidebarOpen = payload;
    },
    SET_CHAT_SIDEBAR_COLLAPSED(payload: boolean) {
      this.chatSidebarCollapsed = payload;
    },
    // 2026-07-21 chatui sidebar resize (elecvoid243): persist
    // user-resized width. Always clamp on the way in so a stale
    // localStorage value from an earlier schema can't produce a
    // negative or runaway width.
    SET_CHAT_SIDEBAR_WIDTH(payload: number) {
      const clamped = clampChatSidebarWidth(payload);
      this.chatSidebarWidth = clamped;
      if (typeof localStorage !== "undefined") {
        localStorage.setItem(CHAT_SIDEBAR_WIDTH_KEY, String(clamped));
      }
    },
    SET_CHAT_SIDEBAR_RESIZING(payload: boolean) {
      this.chatSidebarResizing = payload;
    },
  },
});
