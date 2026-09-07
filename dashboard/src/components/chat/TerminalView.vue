<!-- Author: elecvoid243 @ 2026-09-01
     Terminal sub-page: persistent shell (PowerShell/cmd), xterm.js
     rendering, keyboard input INSIDE the terminal (no external input
     field). No PTY: the shell echoes received lines itself and does NOT
     handle DEL, so the frontend runs a local line editor:
       - printable chars are echoed locally only (never sent);
       - backspace / history edit the local buffer only;
       - Enter keeps the local preview on screen, sends ONE complete
         line, and the shell's own echo of the line is swallowed from
         the output stream so the command shows exactly once.
     This keeps single display + correct backspace for both shells. -->
<template>
  <div class="terminal-view">
    <div class="terminal-toolbar">
      <v-btn-toggle
        v-model="shell"
        mandatory
        variant="outlined"
        class="terminal-shell-toggle"
        :disabled="running"
      >
        <!-- Explicit height prop: VBtnGroup forces inline height:auto on
             children, which no CSS rule can override. -->
        <v-btn
          value="powershell"
          height="24"
          class="terminal-toggle-btn"
          :ripple="false"
        >
          PowerShell
        </v-btn>
        <v-btn
          value="cmd"
          height="24"
          class="terminal-toggle-btn"
          :ripple="false"
        >
          cmd
        </v-btn>
      </v-btn-toggle>
      <div class="terminal-status" :class="`is-${status}`">
        <span class="terminal-status-dot" />
        {{ statusLabel }}
      </div>
      <div class="terminal-actions">
        <v-btn
          v-if="!running"
          variant="tonal"
          size="small"
          color="primary"
          class="terminal-action-btn"
          :disabled="busy || !props.umo"
          @click="onStart"
        >
          <v-icon size="14" start>mdi-console-line</v-icon>
          {{ tm("spcodeProjectLoad.gitDiffSidebar.terminal.connect") }}
        </v-btn>
        <template v-else>
          <v-btn
            variant="text"
            size="small"
            class="terminal-action-btn"
            @click="doInterrupt"
          >
            <v-icon size="14" start>mdi-stop-circle-outline</v-icon>
            {{ tm("spcodeProjectLoad.gitDiffSidebar.terminal.interrupt") }}
          </v-btn>
          <v-btn
            variant="tonal"
            size="small"
            color="error"
            class="terminal-action-btn"
            @click="onStop"
          >
            <v-icon size="14" start>mdi-power</v-icon>
            {{ tm("spcodeProjectLoad.gitDiffSidebar.terminal.stop") }}
          </v-btn>
        </template>
        <v-btn
          variant="text"
          size="small"
          class="terminal-action-btn"
          @click="onClear"
        >
          <v-icon size="14" start>mdi-eraser</v-icon>
          {{ tm("spcodeProjectLoad.gitDiffSidebar.terminal.clear") }}
        </v-btn>
      </div>
    </div>
    <div ref="hostRef" class="terminal-host" />
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import { pluginExtensionApi } from "@/api/v1";
import { fetchWithAuth } from "@/api/http";
import { useModuleI18n } from "@/i18n/composables";
import {
  parseSpcodeTerminalStreamBlock,
  splitSpcodeTerminalBlocks,
} from "@/composables/parseSpcodeTerminalStream";

interface TerminalStartData {
  session_id: string;
  pid: number;
  shell: string;
  cwd: string;
  status: string;
}

interface TerminalStatusData {
  session_id: string;
  pid: number;
  status: string;
  stdout: string;
  exit_code: number | null;
  cursor: number;
  has_more: boolean;
  error?: string;
}

const props = defineProps<{
  umo: string | null;
  projectRoot: string | null;
  isDark: boolean;
}>();

const { tm } = useModuleI18n("features/chat");

const SHELL_KEY = "astrbot.spcode.gitDiffSidebar.terminalShell";

function loadShell(): "powershell" | "cmd" {
  try {
    const v = localStorage.getItem(SHELL_KEY);
    if (v === "cmd") return "cmd";
  } catch {
    /* localStorage unavailable (private mode) */
  }
  return "powershell";
}

const shell = ref<"powershell" | "cmd">(loadShell());
const status = ref<"idle" | "running" | "exited" | "error">("idle");
const busy = ref(false);
const sessionId = ref<string | null>(null);
const hostRef = ref<HTMLDivElement | null>(null);
let term: Terminal | null = null;
let fit: FitAddon | null = null;
let abortController: AbortController | null = null;
let resizeObserver: ResizeObserver | null = null;
// Local line editor state (frontend-only; the shell never sees partial
// input, only complete lines on Enter).
let pendingLine = "";
let pendingCols = 0;
const history: string[] = [];
let historyIndex = -1;
// Shell-echo swallow state: the exact last line sent, plus how many of
// its characters already matched in the output stream. PowerShell on a
// piped stdin echoes each received line back; swallowing that echo
// keeps the locally echoed command as the only copy on screen.
let echoLine: string | null = null;
let echoPos = 0;

const running = computed(() => status.value === "running");

const statusLabel = computed(() => {
  const t = tm as (key: string) => string;
  switch (status.value) {
    case "running":
      return t("spcodeProjectLoad.gitDiffSidebar.terminal.connected");
    case "exited":
      return t("spcodeProjectLoad.gitDiffSidebar.terminal.exited");
    case "error":
      return t("spcodeProjectLoad.gitDiffSidebar.terminal.connectionError");
    default:
      return t("spcodeProjectLoad.gitDiffSidebar.terminal.noSession");
  }
});

/** ANSI color helper: wrap text in a foreground color without resets. */
function ansi(color: string, text: string): string {
  return `\x1b[${color}m${text}\x1b[0m`;
}

/** Approximate display column width (East Asian wide chars count as 2). */
function strWidth(s: string): number {
  let w = 0;
  for (const ch of s) {
    const code = ch.codePointAt(0) ?? 0;
    w +=
      (code >= 0x1100 &&
        (code <= 0x115f ||
          code === 0x2329 ||
          code === 0x232a ||
          (code >= 0x2e80 && code <= 0xa4cf && code !== 0x303f) ||
          (code >= 0xac00 && code <= 0xd7a3) ||
          (code >= 0xf900 && code <= 0xfaff) ||
          (code >= 0xfe10 && code <= 0xfe19) ||
          (code >= 0xfe30 && code <= 0xfe6f) ||
          (code >= 0xff00 && code <= 0xff60) ||
          (code >= 0xffe0 && code <= 0xffe6) ||
          (code >= 0x20000 && code <= 0x2fffd) ||
          (code >= 0x30000 && code <= 0x3fffd)))
        ? 2
        : 1;
  }
  return w;
}

/** Erase the locally echoed input preview (cursor back + clear tail). */
function clearLocalInput(): void {
  if (pendingCols > 0) {
    term?.write(`\x1b[${pendingCols}D\x1b[0K`);
  }
  pendingCols = 0;
}

/** Redraw the local input preview from scratch on the current line. */
function redrawLocalInput(): void {
  term?.write(pendingLine);
  pendingCols = strWidth(pendingLine);
}

/**
 * Swallow the shell's own echo of the submitted line from an output
 * chunk, so the locally echoed preview stays the only copy on screen.
 * The echo is the exact line text followed by a newline and may be
 * split across chunks; if the stream does not match (e.g. cmd /Q,
 * which never echoes), the text is displayed unchanged.
 *
 * Args:
 *   text: Raw decoded output chunk from the SSE stream.
 *
 * Returns:
 *   The part of the chunk that should be written to the terminal.
 */
function swallowShellEcho(text: string): string {
  if (echoLine === null) return text;
  let matched = 0; // echo chars matched within this chunk
  let i = 0;
  while (i < text.length) {
    const ch = text[i];
    if (echoPos < echoLine.length) {
      if (ch === echoLine[echoPos]) {
        echoPos += 1;
        matched += 1;
        i += 1;
        continue;
      }
      // Not the echo after all: show the rest, re-emitting what this
      // chunk already consumed.
      echoLine = null;
      echoPos = 0;
      return text.slice(i - matched);
    }
    // Full line matched; the echo ends with a newline.
    if (ch === "\r" || ch === "\n") {
      if (ch === "\r" && text[i + 1] === "\n") i += 1;
      i += 1;
      echoLine = null;
      echoPos = 0;
      return text.slice(i);
    }
    // No newline right after the line — not an echo.
    echoLine = null;
    echoPos = 0;
    return text.slice(i - matched);
  }
  // The whole chunk was part of the echo; nothing left to display.
  return "";
}

function writeNotice(text: string): void {
  term?.write(`${ansi("90", text)}\r\n`);
}

// ------------------------------------------------------------------
// Local line editor (raw terminal input)
// ------------------------------------------------------------------
function onTerminalData(data: string): void {
  if (!sessionId.value) return;
  // Enter FIRST: CR/LF is a control char, so it must be handled before
  // the generic control-char filter below.
  if (data === "\r" || data === "\n") {
    submitPendingLine();
    return;
  }
  // Ctrl+C -> interrupt, clear the preview.
  if (data === "\x03") {
    pendingLine = "";
    clearLocalInput();
    echoLine = null;
    echoPos = 0;
    term?.write("^C\r\n");
    void doInterrupt();
    return;
  }
  // Backspace: local edit only (DEL never reaches the shell). Checked
  // before the control-char filter: BS (0x08) is a control char too.
  if (data === "\x7f" || data === "\x08") {
    if (pendingLine.length > 0) {
      pendingLine = pendingLine.slice(0, -1);
      term?.write("\b \b");
      pendingCols = Math.max(0, pendingCols - 1);
    }
    return;
  }
  // Arrow keys: local command history (no send).
  if (data === "\x1b[A") {
    historyPrev();
    return;
  }
  if (data === "\x1b[B") {
    historyNext();
    return;
  }
  // Ignore other escape sequences / control chars (Tab, Alt-combos…).
  if (data === "\x1b" || data.startsWith("\x1b") || /[\x00-\x1f]/.test(data)) {
    return;
  }
  // Printable chars: local echo only.
  pendingLine += data;
  pendingCols += strWidth(data);
  term?.write(data);
}

function submitPendingLine(): void {
  const line = pendingLine;
  pendingLine = "";
  pendingCols = 0;
  // Keep the locally echoed command on screen and just advance to the
  // next line — the shell's own echo of the line is swallowed in the
  // SSE stream, so the command is displayed exactly once.
  term?.write("\r\n");
  if (line.trim()) {
    history.push(line);
    // A just-executed line becomes the "current" line again: reset the
    // browse pointer so the next ↑ starts from the newest command
    // instead of resuming the previous mid-history position.
    historyIndex = -1;
  }
  echoLine = line;
  echoPos = 0;
  void sendInput(`${line}\n`);
}

function historyPrev(): void {
  if (history.length === 0) return;
  if (historyIndex === -1) {
    historyIndex = history.length - 1;
  } else if (historyIndex > 0) {
    historyIndex -= 1;
  }
  applyHistoryLine(history[historyIndex] ?? "");
}

function historyNext(): void {
  if (history.length === 0 || historyIndex === -1) return;
  historyIndex += 1;
  if (historyIndex >= history.length) {
    historyIndex = -1;
    applyHistoryLine("");
    return;
  }
  applyHistoryLine(history[historyIndex] ?? "");
}

function applyHistoryLine(line: string): void {
  clearLocalInput();
  pendingLine = line;
  redrawLocalInput();
}

function sendInput(chars: string): void {
  if (!props.umo || !sessionId.value) return;
  void pluginExtensionApi
    .post("spcode/terminal/input", {
      umo: props.umo,
      session_id: sessionId.value,
      chars,
    })
    .catch(() => {
      /* transient input loss — SSE error handling covers the rest */
    });
}

async function doInterrupt(): Promise<void> {
  if (!props.umo || !sessionId.value) return;
  try {
    await pluginExtensionApi.post("spcode/terminal/interrupt", {
      umo: props.umo,
      session_id: sessionId.value,
    });
  } catch {
    /* ignore */
  }
}

/** Dark editor palette (GitHub Dark-like). */
const DARK_THEME = {
  background: "#101418",
  foreground: "#d6dde5",
  cursor: "#6ab4ff",
  cursorAccent: "#101418",
  selectionBackground: "rgba(106, 180, 255, 0.28)",
  black: "#101418",
  brightBlack: "#5c6770",
  blue: "#6ab4ff",
  brightBlue: "#8cc6ff",
  green: "#7ec699",
  brightGreen: "#9adbad",
  cyan: "#56c8d8",
  brightCyan: "#7ee0ee",
  yellow: "#d8b76a",
  brightYellow: "#f0d08a",
  red: "#e07070",
  brightRed: "#f08a8a",
  magenta: "#c48ad8",
  brightMagenta: "#d9a8ea",
  white: "#d6dde5",
  brightWhite: "#ffffff",
} as const;

/** Light editor palette (GitHub Light-like). */
const LIGHT_THEME = {
  background: "#fbfbf8",
  foreground: "#24292f",
  cursor: "#0969da",
  cursorAccent: "#fbfbf8",
  selectionBackground: "rgba(9, 105, 218, 0.18)",
  black: "#24292f",
  brightBlack: "#6e7781",
  blue: "#0969da",
  brightBlue: "#218bff",
  green: "#1a7f37",
  brightGreen: "#2da44e",
  cyan: "#1b7c83",
  brightCyan: "#3192aa",
  yellow: "#9a6700",
  brightYellow: "#bf8700",
  red: "#cf222e",
  brightRed: "#f14c4c",
  magenta: "#8250df",
  brightMagenta: "#a475f9",
  white: "#24292f",
  brightWhite: "#1f2328",
} as const;

function initTerm(): void {
  if (!hostRef.value) return;
  const host = hostRef.value;
  term = new Terminal({
    fontSize: 12,
    fontFamily: 'Consolas, "Courier New", monospace',
    convertEol: true,
    cursorBlink: true,
    theme: props.isDark ? DARK_THEME : LIGHT_THEME,
  });
  fit = new FitAddon();
  term.loadAddon(fit);
  term.open(host);
  // Focus the terminal on click so keyboard input lands in xterm.
  host.addEventListener("mousedown", () => term?.focus());
  term.onData(onTerminalData);
  void nextTick(() => fit?.fit());

  resizeObserver = new ResizeObserver(() => fit?.fit());
  resizeObserver.observe(host);
}

async function onStart(): Promise<void> {
  if (!props.umo || busy.value) return;
  busy.value = true;
  try {
    const resp = await pluginExtensionApi.post<TerminalStartData>(
      "spcode/terminal/start",
      { umo: props.umo, shell: shell.value, cwd: props.projectRoot ?? "" },
    );
    const data = resp.data?.data;
    if (!data?.session_id) {
      status.value = "error";
      writeNotice(tm("spcodeProjectLoad.gitDiffSidebar.terminal.startFailed"));
      return;
    }
    try {
      localStorage.setItem(SHELL_KEY, shell.value);
    } catch {
      /* ignore */
    }
    sessionId.value = data.session_id;
    status.value = "running";
    // A start always replaces the previous session (the backend
    // terminates it), so begin on a clean screen — covers switching
    // shells (PowerShell <-> cmd) as well as same-shell restarts.
    term?.reset();
    writeNotice(`[spcode] ${data.shell} @ ${data.cwd} (pid ${data.pid})`);
    await openStream(0);
    term?.focus();
  } finally {
    busy.value = false;
  }
}

async function openStream(startCursor: number): Promise<void> {
  if (!props.umo || !sessionId.value) return;
  abortController?.abort();
  abortController = new AbortController();
  const url =
    `/api/v1/plugins/extensions/spcode/terminal/stream` +
    `?umo=${encodeURIComponent(props.umo)}` +
    `&session_id=${encodeURIComponent(sessionId.value)}` +
    `&cursor=${startCursor}`;
  try {
    const resp = await fetchWithAuth(url, { signal: abortController.signal });
    if (!resp.ok || !resp.body) {
      throw new Error(`stream failed: ${resp.status}`);
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const { blocks, remainder } = splitSpcodeTerminalBlocks(buffer);
      buffer = remainder;
      for (const block of blocks) {
        const event = parseSpcodeTerminalStreamBlock(block);
        if (!event) continue;
        if (event.type === "output") {
          term?.write(swallowShellEcho(String(event.data)));
        } else if (event.type === "exit") {
          status.value = "exited";
          writeNotice(
            `[spcode] session exited (code ${
              (event.data as { code?: number | null }).code ?? "?"
            })`,
          );
          sessionId.value = null;
          pendingLine = "";
          pendingCols = 0;
          echoLine = null;
          echoPos = 0;
          return;
        } else if (event.type === "error") {
          status.value = "error";
          sessionId.value = null;
          pendingLine = "";
          pendingCols = 0;
          echoLine = null;
          echoPos = 0;
          writeNotice(String(event.data));
          return;
        }
      }
    }
  } catch {
    if (abortController?.signal.aborted) return;
    status.value = "error";
    writeNotice(tm("spcodeProjectLoad.gitDiffSidebar.terminal.connectionError"));
  }
}

async function restoreSession(): Promise<void> {
  if (!props.umo) return;
  try {
    const resp = await pluginExtensionApi.get<TerminalStatusData>(
      "spcode/terminal/status",
      { params: { umo: props.umo } },
    );
    const data = resp.data?.data;
    if (!data?.session_id || data.error === "no_session") return;
    sessionId.value = data.session_id;
    if (data.stdout) term?.write(data.stdout);
    status.value = data.status === "running" ? "running" : "exited";
    if (data.status === "running") {
      await openStream(data.cursor ?? 0);
      term?.focus();
    } else {
      sessionId.value = null;
    }
  } catch {
    /* offline status probe — stays idle */
  }
}

async function onStop(): Promise<void> {
  if (!props.umo || !sessionId.value) return;
  // Abort the SSE first so the session-cleanup error event (poll of a
  // removed session) cannot overwrite the manual "idle" status below.
  abortController?.abort();
  try {
    await pluginExtensionApi.post("spcode/terminal/stop", {
      umo: props.umo,
      session_id: sessionId.value,
    });
  } catch {
    /* ignore */
  }
  term?.write("\r\n[spcode] session terminated\r\n");
  status.value = "idle";
  sessionId.value = null;
  pendingLine = "";
  pendingCols = 0;
  echoLine = null;
  echoPos = 0;
}

function onClear(): void {
  term?.clear();
}

onMounted(() => {
  initTerm();
  // Recover a live session after e.g. a browser refresh.
  void restoreSession();
});

// Apply theme changes to a live terminal (global dark/light toggle).
watch(
  () => props.isDark,
  (dark) => {
    if (term) term.options.theme = dark ? DARK_THEME : LIGHT_THEME;
  },
);

onBeforeUnmount(() => {
  abortController?.abort();
  resizeObserver?.disconnect();
  term?.dispose();
  term = null;
  fit = null;
  // Deliberately NOT terminating the process: the shell keeps running so
  // switching tabs does not kill long-running tasks.
});
</script>

<style scoped>
.terminal-view {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 320px;
}
.terminal-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 4px 0 8px;
  flex-wrap: wrap;
}
/* Must beat `.v-btn-group--density-default.v-btn-group { height: 48px }`:
   the toggle root has a fixed 48px height by default, which would make
   the toolbar row misalign around the 24px buttons. */
.terminal-view .terminal-shell-toggle {
  height: 24px;
  border-radius: 6px;
  flex: none;
}
/* Direct class on the toggle v-btns (same pattern as the action
   buttons): the scoped attribute lands on the button root itself. */
.terminal-toggle-btn {
  min-width: 0;
  padding: 0 10px;
  text-transform: none;
  font-size: 11px;
  letter-spacing: 0;
}
/* Status hint: plain dot + text, deliberately no chip background. */
.terminal-status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  font-size: 12px;
  color: rgba(var(--v-theme-on-surface), 0.55);
  overflow: hidden;
  white-space: nowrap;
}
.terminal-status-dot {
  flex: none;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: currentColor;
}
.terminal-status.is-running {
  color: rgb(var(--v-theme-success));
}
.terminal-status.is-exited {
  color: rgb(var(--v-theme-warning));
}
.terminal-status.is-error {
  color: rgb(var(--v-theme-error));
}
.terminal-status.is-running .terminal-status-dot {
  animation: terminal-status-pulse 1.6s ease-in-out infinite;
}
@keyframes terminal-status-pulse {
  50% {
    opacity: 0.3;
  }
}
/* Right-aligned action group (connect / interrupt / stop / clear). */
.terminal-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  margin-left: auto;
  flex: none;
}
.terminal-action-btn {
  height: 24px;
  padding: 0 8px;
  text-transform: none;
  font-size: 12px;
  letter-spacing: 0;
}
.terminal-host {
  flex: 1;
  min-height: 0;
  border-radius: 8px;
  overflow: hidden;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.1);
}
.terminal-host :deep(.xterm) {
  height: 100%;
  padding: 8px 10px;
}
</style>
