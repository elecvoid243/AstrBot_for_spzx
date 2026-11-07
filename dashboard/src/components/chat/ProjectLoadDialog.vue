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
  force?: boolean;
  /** Legacy chat-command text, dispatched verbatim when no session exists. */
  legacyText: string;
}
</script>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useModuleI18n } from "@/i18n/composables";
import { useProjectPathHistory } from "@/composables/useProjectPathHistory";
import ProjectDirectoryBrowser from "./ProjectDirectoryBrowser.vue";
import SpSegmentedControl from "./SpSegmentedControl.vue";

/**
 * Compose the final chat input text for the load/set command.
 *
 * Args:
 *   wakePrefix: Wake prefix configured for chat commands.
 *   path: User-entered project path.
 *   cmdMode: Project-load or Codegraph-set command mode.
 *   extraFlags: Additional ``/project load`` flags appended verbatim
 *     (currently only ``replace``). Ignored for the ``codegraph``
 *     command mode. Defaults to no extra flags.
 *
 * Returns:
 *   The complete command string to submit through ChatInput.
 */
function buildLoadCommand(
  wakePrefix: string,
  path: string,
  cmdMode: "project" | "codegraph" | "codegraph-init",
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
  const flags = cmdMode === "project" ? extraFlags : [];
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

// ── Derived ─────────────────────────────────────────────────────────────
const dialogTitle = computed(() =>
  props.commandMode === "codegraph"
    ? tm("spcodeProjectLoad.dialog.codegraphTitle")
    : tm("spcodeProjectLoad.dialog.title"),
);

// ── Codegraph 管理双子页(2026-09-08) ───────────────────────────────────
// "初始化 / 更新" → POST /spcode/codegraph-init（codegraph init，已初始化
// 时后端自动 --force 重建）；"设置默认目录" → POST /spcode/codegraph-set
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

// Shared recent-path history (localStorage-backed singleton); also fed
// by ProjectDialog so both entry points see the same recents.
const { recentPaths, addToPathHistory, removeFromPathHistory } =
  useProjectPathHistory();
const canSubmit = computed(() => path.value.trim().length > 0);

// Reset transient input when the dialog opens (restore every option to its
// default; preserve the history dropdown). The path is cleared explicitly in
// `openLoadDialog()` so the "浏览…" picker can pre-fill it when opened from
// within the dialog (2026-08-15).
watch(dialogOpen, (open) => {
  if (open) {
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
 * For the ``project`` mode the backend validates the new path and loads
 * its AGENTS.md before swapping out an already-loaded project, so the
 * dialog can always submit ``replace`` without a confirmation prompt: a
 * failed load leaves the previous project intact.
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
      ),
    });
    dialogOpen.value = false;
    return;
  }

  addToPathHistory(trimmed);
  emit("submit", {
    mode: "project",
    path: trimmed,
    // Atomic replace: the backend swaps only after the new path is valid
    // and its AGENTS.md has loaded, so no confirmation is needed.
    force: true,
    legacyText: buildLoadCommand(prefix, trimmed, "project", ["replace"]),
  });
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
        <v-form @submit.prevent="onConfirm">
          <!--
            Codegraph 管理双子页(2026-09-08):"初始化 / 更新" 对指定目录
            执行 codegraph init(已初始化时后端自动 --force 重建索引);
            "设置默认目录" 执行 codegraph set(会重启 MCP)。两页共用下方
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

          <div
            v-if="props.commandMode === 'project'"
            class="text-caption text-medium-emphasis mb-3"
          >
            {{ tm("spcodeProjectLoad.dialog.loadHint") }}
          </div>

          <ProjectDirectoryBrowser
            v-model="browserOpen"
            @select="onBrowserSelect"
          />

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

</style>
