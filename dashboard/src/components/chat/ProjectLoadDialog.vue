<script lang="ts">
/**
 * Structured payload emitted by ProjectLoadDialog on confirm / unload.
 *
 * 2026-08-06: the dialog no longer emits a raw chat-command string. It
 * emits this payload so ChatInput can dispatch the silent webapi call
 * (no chat-history pollution). `legacyText` keeps the exact command text
 * for the no-session fallback path (dispatched verbatim as before).
 */
export interface ProjectLoadSubmitPayload {
  mode: "project" | "codegraph" | "codegraph-init" | "unload";
  path?: string;
  noAgentsmd?: boolean;
  noCodegraph?: boolean;
  create?: boolean;
  gitInit?: boolean;
  force?: boolean;
  /** Legacy chat-command text, dispatched verbatim when no session exists. */
  legacyText: string;
}
</script>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useModuleI18n } from "@/i18n/composables";
import { useSpcodeProjectStatus } from "@/composables/useSpcodeProjectStatus";
import { useProjectPathHistory } from "@/composables/useProjectPathHistory";
import { useConfirmDialog } from "@/utils/confirmDialog";
import ProjectDirectoryBrowser from "./ProjectDirectoryBrowser.vue";
import SpSegmentedControl from "./SpSegmentedControl.vue";
import SpToggleChip from "./SpToggleChip.vue";

/**
 * Compose the final chat input text for the load/set command.
 *
 * Args:
 *   wakePrefix: Wake prefix configured for chat commands.
 *   path: User-entered project path.
 *   cmdMode: Project-load or Codegraph-set command mode.
 *   loadAgentsMd: Whether the project load should run AGENTS.md steps.
 *   loadCodegraph: Whether the project load should run Codegraph steps.
 *   extraFlags: Additional ``/project load`` flags appended verbatim
 *     (e.g. ``create`` / ``git_init`` / ``replace``). Ignored for the
 *     ``codegraph`` command mode. Defaults to no extra flags.
 *
 * Returns:
 *   The complete command string to submit through ChatInput.
 */
function buildLoadCommand(
  wakePrefix: string,
  path: string,
  cmdMode: "project" | "codegraph" | "codegraph-init",
  loadAgentsMd: boolean,
  loadCodegraph: boolean,
  extraFlags: string[] = [],
): string {
  const prefix = wakePrefix || "/";
  const trimmed = path.trim();
  const alreadyQuoted =
    trimmed.startsWith('"') && trimmed.endsWith('"') && trimmed.length >= 2;
  const needsQuoting = !alreadyQuoted && /\s/.test(trimmed);
  const finalPath = needsQuoting ? `"${trimmed}"` : trimmed;
  const verb =
    cmdMode === "codegraph-init"
      ? "codegraph init"
      : cmdMode === "codegraph"
        ? "codegraph set"
        : "project load";
  const flags =
    cmdMode === "project"
      ? [
          loadAgentsMd ? "" : "no_agentsmd",
          loadCodegraph ? "" : "no_codegraph",
          ...extraFlags,
        ].filter(Boolean)
      : [];
  return `${prefix}${verb} ${[finalPath, ...flags].join(" ")}`;
}

// ── Props / Emits ───────────────────────────────────────────────────────
interface Props {
  wakePrefixes: string[];
  /**
   * Controls the command and UI layout:
   * - `'project'` (default): ``/project load <path>`` with the
   *   mode/kind/options UI and an unload button.
   * - `'codegraph'`: ``/codegraph set <path>``, no unload button and
   *   none of the project-only options (unchanged from before).
   */
  commandMode?: "project" | "codegraph";
}

const props = withDefaults(defineProps<Props>(), {
  wakePrefixes: () => ["/"],
  commandMode: "project",
});

const emit = defineEmits<{
  submit: [payload: ProjectLoadSubmitPayload];
}>();

// ── i18n / shared singletons ────────────────────────────────────────────
const { tm } = useModuleI18n("features/chat");
// Module-level singletons: the loaded-project chip and the confirm
// dialog are app-wide, so reading them here needs no prop plumbing.
const spcodeStatus = useSpcodeProjectStatus();
const confirmDialog = useConfirmDialog();

// ── Derived ─────────────────────────────────────────────────────────────
const dialogTitle = computed(() =>
  props.commandMode === "codegraph"
    ? tm("spcodeProjectLoad.dialog.codegraphTitle")
    : tm("spcodeProjectLoad.dialog.title"),
);

// ── Codegraph 管理双子页(2026-09-08) ───────────────────────────────────
// "初始化 / 更新" → POST /spcode/codegraph-init（codegraph init，已初始化
// 时后端自动 --force 重建）；"设为默认目录" → POST /spcode/codegraph-set
// （原有行为）。两页共用下方的路径输入 + 历史列表。
const codegraphTab = ref<string>("init");
const codegraphSegments = computed(() => [
  { value: "init", label: tm("spcodeProjectLoad.dialog.codegraphTabInit") },
  { value: "set", label: tm("spcodeProjectLoad.dialog.codegraphTabSet") },
]);
const codegraphHint = computed(() =>
  codegraphTab.value === "init"
    ? tm("spcodeProjectLoad.dialog.codegraphInitHint")
    : tm("spcodeProjectLoad.dialog.codegraphSetHint"),
);
const submitLabel = computed(() => {
  if (props.commandMode !== "codegraph") {
    return tm("spcodeProjectLoad.dialog.submit");
  }
  return codegraphTab.value === "init"
    ? tm("spcodeProjectLoad.dialog.codegraphInitSubmit")
    : tm("spcodeProjectLoad.dialog.codegraphSetSubmit");
});

// ── Reactive state ──────────────────────────────────────────────────────
const dialogOpen = ref(false);
const path = ref("");
// 应用内目录选择器开关(2026-08-15):"浏览…"按钮打开它,选中绝对路径回填 path。
const browserOpen = ref(false);
// Top-level choice: load an existing project vs. create a new one.
const loadMode = ref<"existing" | "create">("existing");
// Only meaningful when loadMode === "existing": code project (loads
// AGENTS.md + Codegraph by default) vs. plain project (loads neither).
const projectKind = ref<"code" | "plain">("code");
// Always-visible checkboxes for the existing + code branch.
const loadAgentsMd = ref(true);
const loadCodegraph = ref(true);
// Only meaningful when loadMode === "create".
const autoInitGit = ref(true);

// Shared recent-path history (localStorage-backed singleton); also fed
// by ProjectDialog so both entry points see the same recents.
const { recentPaths, addToPathHistory, removeFromPathHistory } =
  useProjectPathHistory();
const canSubmit = computed(() => path.value.trim().length > 0);

// Switching the project kind re-applies the spec's default selection
// (code → both on, plain → both off). The user may still override the
// checkboxes manually afterwards — this only fires on the kind toggle.
watch(projectKind, (kind) => {
  const isCode = kind === "code";
  loadAgentsMd.value = isCode;
  loadCodegraph.value = isCode;
});

// Reset transient input when the dialog opens (restore every option to its
// default; preserve the history dropdown). The path is cleared explicitly in
// `openLoadDialog()` so the "浏览…" picker can pre-fill it when opened from
// within the dialog (2026-08-15).
watch(dialogOpen, (open) => {
  if (open) {
    loadMode.value = "existing";
    projectKind.value = "code";
    loadAgentsMd.value = true;
    loadCodegraph.value = true;
    autoInitGit.value = true;
    codegraphTab.value = "init";
  }
});

// ── Public API (exposed) ───────────────────────────────────────────────
/**
 * Open the dialog. Exposed via ``defineExpose`` so external triggers
 * (the spcode "currently loaded project" chip, or the ``+`` menu
 * entry's slim trigger) can summon the same dialog from anywhere in
 * the app.
 */
function openLoadDialog(): void {
  path.value = "";
  dialogOpen.value = true;
}

function closeLoadDialog(): void {
  dialogOpen.value = false;
}

defineExpose({ openLoadDialog, closeLoadDialog });

// ── Handlers ────────────────────────────────────────────────────────────
/**
 * Handle a directory chosen in the in-app file browser: fill the path
 * field. The selected absolute path comes straight from the backend
 * (ProjectDirectoryBrowser only ever emits backend-returned paths), so it
 * flows into the exact same submit chain as a manually typed path — text
 * input remains fully available (2026-08-15).
 */
function onBrowserSelect(selectedPath: string): void {
  path.value = selectedPath;
}

/**
 * Confirm the dialog: build the command for the current mode and emit
 * it through ChatInput.
 *
 * For the ``project`` mode the effective AGENTS.md / Codegraph flags and
 * the extra ``create`` / ``git_init`` / ``replace`` flags are derived
 * from the mode/kind/options UI. When a project is already loaded we
 * ask the user whether to overwrite; on confirm we append ``replace`` so
 * the backend atomically unloads the old project inside the same load
 * flow (the frontend deliberately does NOT fire ``/project unload`` and
 * ``/project load`` as two chat commands — unload's state cleanup is
 * async, so a closely-following load could still see the old project
 * and be rejected). Cancelling the overwrite prompt leaves the dialog
 * open so the user can adjust their input.
 */
async function onConfirm(): Promise<void> {
  const trimmed = path.value.trim();
  if (!trimmed) return;
  const prefix = props.wakePrefixes[0] || "/";

  // Codegraph mode: two sub-pages sharing the path field. "init" hits
  // POST /spcode/codegraph-init (codegraph init, backend auto-retries with
  // --force when the directory is already indexed); "set" keeps the legacy
  // POST /spcode/codegraph-set. No mode/kind/overwrite logic either way.
  if (props.commandMode === "codegraph") {
    addToPathHistory(trimmed);
    const isInit = codegraphTab.value === "init";
    emit("submit", {
      mode: isInit ? "codegraph-init" : "codegraph",
      path: trimmed,
      legacyText: buildLoadCommand(
        prefix,
        trimmed,
        isInit ? "codegraph-init" : "codegraph",
        loadAgentsMd.value,
        loadCodegraph.value,
      ),
    });
    dialogOpen.value = false;
    return;
  }

  const isCreate = loadMode.value === "create";
  // New project (spec #3) and existing plain project (spec #2) never
  // load AGENTS.md / Codegraph; existing code project uses the
  // (auto-toggled, user-overridable) checkboxes.
  const effectiveAgentsMd =
    !isCreate && projectKind.value === "code" && loadAgentsMd.value;
  const effectiveCodegraph =
    !isCreate && projectKind.value === "code" && loadCodegraph.value;

  const extraFlags: string[] = [];
  if (isCreate) {
    extraFlags.push("create");
    if (autoInitGit.value) extraFlags.push("git_init");
  }

  // Overwrite confirmation (spec #5).
  let overwriteConfirmed = false;
  if (spcodeStatus.status.value.loaded) {
    const overwriteMsg = tm("spcodeProjectLoad.dialog.overwriteMessage");
    const confirmed = confirmDialog
      ? await confirmDialog({
          title: tm("spcodeProjectLoad.dialog.overwriteTitle"),
          message: overwriteMsg,
        })
      : window.confirm(overwriteMsg);
    if (!confirmed) return; // keep the dialog open
    overwriteConfirmed = true;
    extraFlags.push("replace");
  }

  addToPathHistory(trimmed);
  emit("submit", {
    mode: "project",
    path: trimmed,
    noAgentsmd: !effectiveAgentsMd,
    noCodegraph: !effectiveCodegraph,
    create: isCreate,
    gitInit: isCreate && autoInitGit.value,
    force: overwriteConfirmed,
    legacyText: buildLoadCommand(
      prefix,
      trimmed,
      "project",
      effectiveAgentsMd,
      effectiveCodegraph,
      extraFlags,
    ),
  });
  dialogOpen.value = false;
}

/**
 * Emit the ``/project unload`` command text and close the dialog.
 *
 * No confirmation prompt by design — spcode's unload is idempotent and
 * the user can simply re-load. Mirrors the no-confirm behavior of the
 * load action above.
 */
function onUnload(): void {
  const prefix = props.wakePrefixes[0] || "/";
  emit("submit", { mode: "unload", legacyText: `${prefix}project unload` });
  dialogOpen.value = false;
}
</script>

<template>
  <v-dialog v-model="dialogOpen" max-width="540">
    <v-card>
      <v-card-title class="text-h3 pa-4 pb-0 pl-6">
        {{ dialogTitle }}
      </v-card-title>
      <v-card-text>
        <!-- Unload current project (separate from the load form, above a divider) -->
        <v-btn
          v-if="props.commandMode === 'project'"
          block
          variant="outlined"
          prepend-icon="mdi-folder-remove-outline"
          class="mb-2"
          @click="onUnload"
        >
          {{ tm("spcodeProjectLoad.dialog.unloadButton") }}
        </v-btn>
        <v-divider v-if="props.commandMode === 'project'" class="my-4" />
        <v-form @submit.prevent="onConfirm">
          <!--
            Codegraph 管理双子页(2026-09-08):"初始化 / 更新" 对指定目录
            执行 codegraph init(已初始化时后端自动 --force 重建索引);
            "设为默认目录" 执行 codegraph set(会重启 MCP)。两页共用下方
            路径输入 + 历史列表。
          -->
          <template v-if="props.commandMode === 'codegraph'">
            <SpSegmentedControl
              v-model="codegraphTab"
              :segments="codegraphSegments"
            />
            <div class="text-caption text-medium-emphasis mt-2 mb-3">
              {{ codegraphHint }}
            </div>
          </template>
          <v-text-field
            v-model="path"
            :label="tm('spcodeProjectLoad.dialog.pathLabel')"
            :placeholder="tm('spcodeProjectLoad.dialog.pathPlaceholder')"
            variant="outlined"
            autofocus
            clearable
            @keydown.esc="dialogOpen = false"
          >
            <template #append-inner>
              <v-btn
                icon="mdi-folder-search-outline"
                variant="text"
                size="small"
                data-testid="browse-directory"
                :title="tm('spcodeProjectLoad.dialog.browse')"
                :aria-label="tm('spcodeProjectLoad.dialog.browse')"
                @click="browserOpen = true"
              />
            </template>
          </v-text-field>

          <ProjectDirectoryBrowser
            v-model="browserOpen"
            @select="onBrowserSelect"
          />

          <!--
            Project-only options (spec #1–#3). Replaces the old
            "Advanced settings" expansion panel: the AGENTS.md /
            Codegraph toggles are now always visible, and top-level
            radio groups choose between loading an existing project
            and creating a new one, and between the code / plain
            project kinds.
          -->
          <template v-if="props.commandMode === 'project'">
            <div class="text-caption text-medium-emphasis mb-1">
              {{ tm("spcodeProjectLoad.dialog.modeLabel") }}
            </div>
            <v-radio-group
              v-model="loadMode"
              inline
              density="compact"
              hide-details
              class="option-radio-group mb-3"
            >
              <v-radio
                value="existing"
                :label="tm('spcodeProjectLoad.dialog.modeExisting')"
              />
              <v-radio
                value="create"
                :label="tm('spcodeProjectLoad.dialog.modeCreate')"
              />
            </v-radio-group>

            <template v-if="loadMode === 'existing'">
              <div class="text-caption text-medium-emphasis mb-1">
                {{ tm("spcodeProjectLoad.dialog.kindLabel") }}
              </div>
              <v-radio-group
                v-model="projectKind"
                inline
                density="compact"
                hide-details
                class="option-radio-group mb-2"
              >
                <v-radio
                  value="code"
                  :label="tm('spcodeProjectLoad.dialog.kindCode')"
                />
                <v-radio
                  value="plain"
                  :label="tm('spcodeProjectLoad.dialog.kindPlain')"
                />
              </v-radio-group>
              <div class="text-caption text-medium-emphasis mb-1">
                {{ tm("spcodeProjectLoad.dialog.loadStepsLabel") }}
              </div>
              <!--
                Load-step toggles are pill chips instead of native
                checkboxes (2026-09-09): Vuetify's compact selection
                control centres a 24px glyph inside a 28px wrapper, and
                the old 18px wrapper override here shifted the checkbox
                glyph 5px left of the radio glyphs above. See
                SpToggleChip for the chip itself.
              -->
              <div class="load-chips">
                <SpToggleChip
                  v-model="loadAgentsMd"
                  icon="mdi-file-document-outline"
                  :label="tm('spcodeProjectLoad.dialog.loadAgentsMd')"
                />
                <SpToggleChip
                  v-model="loadCodegraph"
                  icon="mdi-graph-outline"
                  :label="tm('spcodeProjectLoad.dialog.loadCodegraph')"
                />
                <!-- Hint sits on the chip row, right-aligned. -->
                <span class="load-chips__hint">
                  {{ tm("spcodeProjectLoad.dialog.loadAutoCreateHint") }}
                </span>
              </div>
            </template>

            <div v-else class="load-chips">
              <SpToggleChip
                v-model="autoInitGit"
                icon="mdi-source-branch"
                :label="tm('spcodeProjectLoad.dialog.autoInitGit')"
              />
            </div>
          </template>

          <div v-if="recentPaths.length" class="mt-2">
            <div class="text-caption text-medium-emphasis mb-1">
              {{ tm("spcodeProjectLoad.dialog.historyLabel") }}
            </div>
            <v-list density="compact" class="history-list pa-0">
              <v-list-item
                v-for="item in recentPaths"
                :key="item"
                class="history-item"
                rounded="md"
                @click="path = item"
              >
                <template #prepend>
                  <v-icon icon="mdi-history" size="x-small" />
                </template>
                <v-list-item-title class="text-body-2">
                  {{ item }}
                </v-list-item-title>
                <template #append>
                  <!--
                    `@click.stop` prevents the parent v-list-item click
                    (`path = item`) from firing when the user intends to
                    remove the entry.
                  -->
                  <v-btn
                    icon="mdi-close"
                    variant="text"
                    size="x-small"
                    density="compact"
                    :aria-label="tm('spcodeProjectLoad.dialog.removeFromHistory')"
                    @click.stop="removeFromPathHistory(item)"
                  />
                </template>
              </v-list-item>
            </v-list>
          </div>
          <div v-else class="mt-2 text-caption text-medium-emphasis">
            {{ tm("spcodeProjectLoad.dialog.historyEmpty") }}
          </div>
        </v-form>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn variant="text" @click="dialogOpen = false">
          {{ tm("spcodeProjectLoad.dialog.cancel") }}
        </v-btn>
        <v-btn
          color="primary"
          variant="tonal"
          :disabled="!canSubmit"
          @click="onConfirm"
        >
          {{ submitLabel }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.history-list {
  max-height: 160px;
  overflow-y: auto;
  background: transparent;
}

.history-item :deep(.v-list-item-title) {
  font-family: "Fira Code", "Consolas", monospace;
  font-size: 12px;
  word-break: break-all;
  white-space: normal;
}

/*
 * Inline radio groups: remove the default message/details slot spacing
 * so each group sits flush under its caption label, and keep the radio
 * labels at body-2 size to match the rest of the dialog.
 */
.option-radio-group :deep(.v-input__control) {
  gap: 24px;
}

.option-radio-group :deep(.v-selection-control) {
  min-height: 28px;
}

.option-radio-group :deep(.v-label) {
  font-size: 14px;
  opacity: 1;
}

/*
 * Row wrapper for the load-step chips (see SpToggleChip for the chip
 * itself). The 4px inline start aligns the chip border with the visible
 * radio circle: MDI's radiobox ink is inset 2px inside the 24px icon
 * box, which is itself inset 2px inside Vuetify's 28px compact wrapper.
 * (The old 18px wrapper override put the checkbox ink at 0px, i.e. 4px
 * left of the radios.) The hint is pushed to the end of the same row so
 * it reads as part of the toggle group.
 */
.load-chips {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding-inline-start: 4px;
}

.load-chips__hint {
  margin-inline-start: auto;
  font-size: 12px;
  color: rgba(var(--v-theme-on-surface), 0.6);
}
</style>
