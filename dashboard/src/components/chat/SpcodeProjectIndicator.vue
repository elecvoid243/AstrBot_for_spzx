<!--
  Author: elecvoid243, 2026-07-09
  Spec: docs/superpowers/specs/2026-07-09-chat-input-chips-beautify-design.md §5.1, §5.2
  Updated: elecvoid243, 2026-10-03 — single-capsule redesign.

  SpcodeProjectIndicator — ghost capsule for the loaded/unloaded spcode
  project. The 2026-10-03 redesign folds the two side buttons (services
  popover trigger + worktree activation trigger) into ONE capsule whose
  dropdown has three sections:
    1. 项目       — current project (static) + switch/reload entry
    2. LLM 工作树 — which worktree the LLM works in (prompt injection,
                    backend applies it via extra_user_content_parts)
    3. 服务       — codegraph / vivado / tc-memory MCP status + manage
  The active worktree renders directly on the capsule label
  ("project · ⎇ branch") so the pinned state is visible without opening
  anything.

  Visual states:
    - Not loaded → hollow dot ring + folder icon; the dropdown shows the
      "加载项目…" entry plus the services section (codegraph MCP can run
      without a loaded project, so it must stay reachable)
    - Loaded     → success dot + project basename (+ active worktree)
    - Loading    → spinning icon, capsule disabled
    - Failed     → red, dropdown shows the failure log + retry entry

  Event contract (unchanged):
    - Emits `open-load-dialog` / `open-codegraph-dialog`
-->
<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useModuleI18n } from "@/i18n/composables";
import { useSpcodeProjectStatus } from "@/composables/useSpcodeProjectStatus";
import { useSpcodeOperationProgress } from "@/composables/useSpcodeOperationProgress";
import { useSpcodeCodegraphStatus } from "@/composables/useSpcodeCodegraphStatus";
import { useTcMemoryStatus } from "@/composables/useTcMemoryStatus";
import { useSpcodeVivadoStatus } from "@/composables/useSpcodeVivadoStatus";
import { useSpcodeWorktrees } from "@/composables/useSpcodeWorktrees";
import type { SpcodeGitWorktree } from "@/composables/parseSpcodeWorktrees";

const { status } = useSpcodeProjectStatus();
const { tm } = useModuleI18n("features/chat");
const { progress } = useSpcodeOperationProgress();

const emit = defineEmits<{
  (e: "open-load-dialog"): void;
  (e: "open-codegraph-dialog"): void;
}>();

// Only project load/unload operations drive THIS chip. codegraph_set
// progress had a dedicated badge on the removed SpcodeCodegraphChip; the
// services section now reflects codegraph state reactively instead.
const isProjectOp = computed(
  () =>
    progress.value.operation === "project_load" ||
    progress.value.operation === "project_unload",
);
const isLoading = computed(
  () => isProjectOp.value && progress.value.status === "running",
);
const isFailed = computed(
  () => isProjectOp.value && progress.value.status === "failed",
);

/**
 * Show only the basename of a loaded path so the capsule stays compact;
 * the full path is available via the hover tooltip / dropdown.
 */
function pathBasename(path: string): string {
  const trimmed = path.replace(/[\\/]+$/, "");
  const idx = Math.max(trimmed.lastIndexOf("/"), trimmed.lastIndexOf("\\"));
  return idx >= 0 ? trimmed.slice(idx + 1) : trimmed;
}

const displayPath = computed(() =>
  status.value.loaded && status.value.directory
    ? pathBasename(status.value.directory)
    : "",
);

const loadedAtDisplay = computed(() => {
  if (!status.value.loadedAt) return "";
  const ts = status.value.loadedAt;
  const ms = ts > 1e12 ? ts : ts * 1000;
  try {
    const d = new Date(ms);
    if (Number.isNaN(d.getTime())) return "";
    return d.toLocaleString();
  } catch {
    return "";
  }
});

const icon = computed(() => {
  if (isLoading.value) return "mdi-loading";
  if (isFailed.value) return "mdi-alert-circle-outline";
  return status.value.loaded ? "mdi-folder-check-outline" : "mdi-folder-outline";
});

const label = computed(() => {
  // 加载中一律显示统一定位文案(2026-08-15):不再实时打印 yield 的
  // current_step——细节交给状态气泡;加载失败时 yield 信息仍可在下拉中查看。
  if (isLoading.value) {
    return tm("spcodeProjectLoad.indicator.loading");
  }
  if (isFailed.value) return tm("spcodeProjectLoad.indicator.failed");
  return status.value.loaded
    ? tm("spcodeProjectLoad.indicator.loadedLabel")
    : tm("spcodeProjectLoad.indicator.noProject");
});

// Capsule main text: the project basename when loaded (the status dot
// already says "loaded"), status text otherwise.
const capsuleLabel = computed(() =>
  status.value.loaded && !isLoading.value && !isFailed.value
    ? displayPath.value || label.value
    : label.value,
);

const tooltipText = computed(() => {
  if (isLoading.value) return label.value;
  if (isFailed.value) {
    return progress.value.messages.at(-1) ?? label.value;
  }
  if (status.value.loaded) {
    const parts: string[] = [];
    if (status.value.directory) {
      parts.push(
        `${tm("spcodeProjectLoad.indicator.loadedLabel")} ${status.value.directory}`,
      );
    }
    if (loadedAtDisplay.value) {
      parts.push(
        `${tm("spcodeProjectLoad.indicator.loadedAtPrefix")}: ${loadedAtDisplay.value}`,
      );
    }
    if (activeWorktreeLabel.value) {
      parts.push(
        tm("spcodeProjectLoad.indicator.worktreeBtnTooltipActive", {
          branch: activeWorktreeLabel.value,
        }),
      );
    }
    return parts.join(" · ");
  }
  return tm("spcodeProjectLoad.indicator.noProject");
});

// ── Unified dropdown (2026-10-03) ─────────────────────────────────────
// One menu hosts three sections: project / LLM worktree / services. The
// data sources are the same module-level singleton composables as before
// (ChatInput's polling/foreground refresh keeps driving them).
const menuOpen = ref(false);

const codegraph = useSpcodeCodegraphStatus();
const vivado = useSpcodeVivadoStatus();

// codegraph 状态只保留 2 态(mcpRunning 单一维度)。
// 2026-09-08: MCP 在跑即视为"已加载"——不再把"未设置默认项目"当作未加载
// 态(system_prompt 已要求每次 codegraph_explore 显式传 projectPath,默认
// 目录缺失不影响 codegraph 可用性);默认目录改在 detail 行提示。
const codegraphState = computed(() => {
  const s = codegraph.status.value;
  const hasProject = s.activeProject.length > 0;
  if (s.mcpRunning) {
    return {
      dot: "success",
      icon: hasProject ? "mdi-database-check" : "mdi-database-outline",
      label: "Codegraph 已加载",
      detail: hasProject
        ? s.activeProject
        : "未设置默认项目(查询时需显式指定目录)",
    };
  }
  return {
    dot: "neutral",
    icon: "mdi-database-off-outline",
    label: "Codegraph 未启动",
    detail: "MCP 未运行, codegraph 不可用",
  };
});

const vivadoState = computed(() => {
  const s = vivado.status.value;
  switch (s.overall) {
    case "ok":
      return {
        dot: "success",
        icon: "mdi-chip",
        label: "Vivado 已就绪",
        detail: s.message,
      };
    case "degraded":
      return {
        dot: "warning",
        icon: "mdi-alert-circle-outline",
        label: "会话数据暂不可用",
        detail: s.message,
      };
    case "not_installed":
      return {
        dot: "error",
        icon: "mdi-package-variant-closed",
        label: "vivado-mcp 未安装",
        detail: s.message,
      };
    case "toolchain_missing":
      return {
        dot: "error",
        icon: "mdi-tools",
        label: "找不到Vivado",
        detail: s.message,
      };
    case "not_running":
      return {
        dot: "neutral",
        icon: "mdi-server-off",
        label: "Vivado 未启动",
        detail: s.message,
      };
    default:
      return {
        dot: "neutral",
        icon: "mdi-server-off-outline",
        label: "Vivado 未启用",
        detail: s.message,
      };
  }
});

// ── Agent Memory（tc_memory 插件）状态条目（2026-10-02 elecvoid243） ──
// 数据源：插件扩展路由 GET /plugins/extensions/astrbot_plugin_tc_memory/status。
// 三态：运行中（绿，detail 带 endpoint/pid）/ 未运行（灰，按 mode 给提示）/
// 未安装（插件未加载或路由不可达）。
const tcMemory = useTcMemoryStatus();

const tcMemoryState = computed(() => {
  const s = tcMemory.status.value;
  if (!s.reachable) {
    return {
      dot: "neutral",
      icon: "mdi-brain-off-outline",
      label: "Agent Memory 未安装",
      detail: "插件未加载或未安装",
    };
  }
  if (s.running) {
    const pidPart = s.pid ? ` · pid ${s.pid}` : "";
    const verPart = s.version ? ` v${s.version}` : "";
    return {
      dot: "success",
      icon: "mdi-brain",
      label: "Agent Memory 运行中",
      detail: `${s.endpoint}${verPart}${pidPart}`,
    };
  }
  return {
    dot: "neutral",
    icon: "mdi-brain-off-outline",
    label: "Agent Memory 未运行",
    detail:
      s.mode === "local" ? "将在插件加载时自动启动" : "无法连接远端服务",
  };
});

/**
 * Codegraph 管理入口:关闭下拉并委托给 ChatInput 打开
 * ``ProjectLoadDialog command-mode="codegraph"``。
 */
function openCodegraphManager(): void {
  menuOpen.value = false;
  emit("open-codegraph-dialog");
}

// ── Worktree 激活 (2026-08-20) ────────────────────────────────────────
// worktree 激活(指定 LLM 工作在哪个 worktree,后端以
// extra_user_content_parts 注入每次 LLM 请求)。2026-10-03 起并入胶囊
// 下拉;激活的分支直接渲染在胶囊标签上。
// 数据与操作复用 useSpcodeWorktrees(GET /spcode/git-worktrees +
// POST /spcode/worktree-activate)。
const worktrees = useSpcodeWorktrees();
const isSelectingWorktree = ref(false);

const worktreeList = computed(() => {
  const s = worktrees.state.value;
  return s.kind === "ok" ? s.snapshot.worktrees : [];
});
const activeWorktree = computed(() => {
  const s = worktrees.state.value;
  return s.kind === "ok" ? s.snapshot.meta.activeWorktree : null;
});
// idle counts as loading so the menu never flashes its empty hint before
// the first refresh resolves.
const worktreesLoading = computed(() => {
  const kind = worktrees.state.value.kind;
  return kind === "loading" || kind === "idle";
});
const worktreesFailed = computed(() => worktrees.state.value.kind === "error");

// 打开下拉时刷新列表(worktree 增删多发生在 GitDiffSidebar/外部,这里不轮询;
// 项目加载/切换由 useSpcodeWorktrees 内部的 umo/directory watcher 自动刷新)。
watch(menuOpen, (open) => {
  if (open && status.value.loaded) void worktrees.refresh();
});

function worktreeLabel(wt: SpcodeGitWorktree): string {
  return wt.branch ?? (wt.isMain
    ? tm("spcodeProjectLoad.indicator.worktreeMainBadge")
    : wt.headSha.slice(0, 7));
}

/** Branch/label of the activated worktree, rendered on the capsule. */
const activeWorktreeLabel = computed(() => {
  const path = activeWorktree.value;
  if (!path) return "";
  const wt = worktreeList.value.find((w) => w.path === path);
  return wt ? worktreeLabel(wt) : pathBasename(path);
});

/**
 * 选中菜单项:null = 未指定(取消激活,LLM 跟随项目路径);
 * 否则激活对应 worktree。成功后关菜单并弹气泡反馈。
 */
async function selectWorktree(path: string | null): Promise<void> {
  if (isSelectingWorktree.value) return;
  isSelectingWorktree.value = true;
  const result = await worktrees.activate({ path });
  isSelectingWorktree.value = false;
  if (!result.ok && result.reason === "aborted") return;
  if (result.ok) {
    menuOpen.value = false;
    const wt = path ? worktreeList.value.find((w) => w.path === path) : null;
    showBubble(
      path === null
        ? tm("spcodeProjectLoad.indicator.worktreeDeactivated")
        : tm("spcodeProjectLoad.indicator.worktreeActivated", {
            branch: wt ? worktreeLabel(wt) : (path ?? ""),
          }),
    );
  } else {
    showBubble(
      tm("spcodeProjectLoad.indicator.worktreeActivateFailed", {
        reason: result.stderr || result.reason,
      }),
    );
  }
}

// ── 状态气泡 (2026-08-15) ─────────────────────────────────────────────
// 原 codegraph chip 移除后,初始化/重启等过程状态失去常驻显示。这里在
// codegraph 状态变更(或 codegraph 相关操作进行中)时,于胶囊旁弹一个
// 漫画式气泡实时提示,3s 后消失;显示期间状态再次更新则重置计时。
const BUBBLE_DURATION_MS = 3000;

const bubbleText = ref("");
const bubbleVisible = ref(false);
let bubbleTimer: number | undefined;

function showBubble(text: string): void {
  bubbleText.value = text;
  bubbleVisible.value = true;
  if (bubbleTimer !== undefined) {
    window.clearTimeout(bubbleTimer);
  }
  bubbleTimer = window.setTimeout(() => {
    bubbleVisible.value = false;
    bubbleTimer = undefined;
  }, BUBBLE_DURATION_MS);
}

/** 立即关闭气泡(✕ 按钮 / 卸载时)。 */
function closeBubble(): void {
  if (bubbleTimer !== undefined) {
    window.clearTimeout(bubbleTimer);
    bubbleTimer = undefined;
  }
  bubbleVisible.value = false;
}

// 挂载后的首次观察只建立基线,不弹气泡——避免打开页面时把
// "已经连接/未连接" 的既有状态误当作变更(否则每次刷新都会弹)。
let bubbleBaselineSet = false;

interface BubbleSnapshot {
  mcp: boolean;
  proj: string;
  op: string | null;
  st: string;
  step: string;
}

/**
 * 由 (codegraph 状态, 操作进度) 推导气泡文案;无值得展示的变化返回 null。
 * 优先级: project_load 阶段 > 进行中的 codegraph 操作 > MCP 状态转变。
 */
function deriveBubbleMessage(now: BubbleSnapshot, prev: BubbleSnapshot): string | null {
  // Polling replaces the progress object even when nothing changed. Only a
  // semantic change may replace the bubble; repeated polls must not extend
  // the 3s lifetime of the current stage.
  const unchanged =
    now.mcp === prev.mcp &&
    now.proj === prev.proj &&
    now.op === prev.op &&
    now.st === prev.st &&
    now.step === prev.step;
  if (unchanged) return null;

  if (now.op === "project_load") {
    // MCP status may catch up while project_load is running or while its
    // completion bubble is visible. Those refreshes must not replace the
    // stage/completion bubble, but later MCP transitions still surface.
    const stageChanged =
      now.op !== prev.op || now.st !== prev.st || now.step !== prev.step;
    if (!stageChanged) {
      if (now.st === "running" || bubbleVisible.value) return null;
    } else if (now.st === "done" && prev.st === "running") {
      return tm("spcodeProjectLoad.indicator.projectLoadComplete");
    } else if (now.st === "running") {
      // agentsmd.init reports the long LLM generation through its own 🔄
      // message, which replaces the outer [1/3] step in current_step.
      if (
        /AGENTS\.md\s*不存在[，,]\s*正在\s*init/i.test(now.step) ||
        /生成\s*AGENTS\.md/i.test(now.step)
      ) {
        return tm("spcodeProjectLoad.indicator.agentsMdInitializing");
      }
      if (/codegraph/i.test(now.step)) {
        return tm("spcodeProjectLoad.indicator.codegraphInitializing");
      }
      return null;
    } else {
      return null;
    }
  }

  if (now.st === "running") {
    const stageChanged =
      now.op !== prev.op || now.st !== prev.st || now.step !== prev.step;
    if (!stageChanged) return null;
    if (now.op === "codegraph_set") {
      return tm("spcodeProjectLoad.indicator.codegraphRestarting");
    }
    if (now.op === "codegraph_init") {
      return tm("spcodeProjectLoad.indicator.codegraphIndexing");
    }
  }
  // MCP state transitions; project_load only suppresses them while a stage
  // is running or its completion bubble is visible.
  if (now.mcp && !prev.mcp) {
    return tm("spcodeProjectLoad.indicator.codegraphConnected");
  }
  if (!now.mcp && prev.mcp) {
    // Running -> stopped. Any active operation state is handled above, so
    // reaching this branch means the service was closed or disconnected.
    return tm("spcodeProjectLoad.indicator.codegraphDisconnected");
  }
  // A project switch while MCP remains running is observable via proj.
  if (now.mcp && now.proj !== prev.proj) {
    return tm("spcodeProjectLoad.indicator.codegraphConnected");
  }
  return null;
}

watch(
  () => ({
    mcp: codegraph.status.value.mcpRunning,
    proj: codegraph.status.value.activeProject,
    op: progress.value.operation,
    st: progress.value.status,
    step: progress.value.currentStep,
  }),
  (now, prev) => {
    if (!bubbleBaselineSet) {
      bubbleBaselineSet = true;
      return;
    }
    const msg = deriveBubbleMessage(now, prev);
    if (msg) showBubble(msg);
  },
  { flush: "post" },
);

onBeforeUnmount(() => {
  if (bubbleTimer !== undefined) {
    window.clearTimeout(bubbleTimer);
    bubbleTimer = undefined;
  }
  worktrees.dispose();
});

function openLoadDialog(): void {
  if (isLoading.value) return; // one silent operation at a time
  emit("open-load-dialog");
}

/** Dropdown "切换或重新加载项目…" entry: close the menu first. */
function handleManageProject(): void {
  menuOpen.value = false;
  openLoadDialog();
}
</script>

<template>
  <div class="sp-chip-wrap">
    <!--
      ONE capsule with the unified dropdown in every state (the services
      section stays reachable even when no project is loaded — codegraph
      MCP can run without one). Only while a load/unload operation is
      running is the capsule disabled. The capsule label is the live
      state — project basename plus the activated LLM worktree branch
      (green) when one is pinned.
    -->
    <v-menu v-model="menuOpen" location="bottom start" transition="none">
      <template #activator="{ props: menuProps }">
        <v-tooltip location="bottom" :open-delay="200">
          <template #activator="{ props: tipProps }">
            <button
              v-bind="{ ...tipProps, ...menuProps }"
              type="button"
              class="sp-capsule"
              :class="{
                'sp-capsule--open': menuOpen,
                'sp-capsule--failed': isFailed,
                'sp-capsule--empty': !status.loaded && !isLoading && !isFailed,
              }"
              :disabled="isLoading"
              :aria-label="tooltipText"
            >
              <span
                class="sp-capsule__dot"
                :class="{
                  'sp-capsule__dot--success':
                    status.loaded && !isLoading && !isFailed,
                  'sp-capsule__dot--warning': isFailed,
                  'sp-capsule__dot--hollow':
                    !status.loaded && !isLoading && !isFailed,
                }"
                aria-hidden="true"
              />
              <v-icon
                v-if="isLoading || isFailed || !status.loaded"
                size="14"
                class="sp-capsule__icon"
                >{{ icon }}</v-icon
              >
              <span class="sp-capsule__label">{{ capsuleLabel }}</span>
              <template v-if="activeWorktreeLabel && !isLoading && !isFailed">
                <span class="sp-capsule__wt-sep" aria-hidden="true">·</span>
                <v-icon size="12" class="sp-capsule__wt-icon">
                  mdi-source-branch
                </v-icon>
                <span class="sp-capsule__wt">{{ activeWorktreeLabel }}</span>
              </template>
              <v-icon size="12" class="sp-capsule__chevron">
                mdi-chevron-down
              </v-icon>
            </button>
          </template>
          <span>{{ tooltipText }}</span>
        </v-tooltip>
      </template>

      <v-card min-width="280" max-width="420">
        <v-card-text>
          <!-- Failed: the dropdown IS the failure log + retry entry. -->
          <template v-if="isFailed">
            <div class="sp-menu-title">
              {{ tm("spcodeProjectLoad.indicator.failedDetailTitle") }}
            </div>
            <pre class="sp-chip-popover-messages">{{
              progress.messages.join("\n")
            }}</pre>
            <div class="sp-menu-divider"></div>
            <button type="button" class="sp-menu-row" @click="handleManageProject">
              <v-icon size="14" class="sp-menu-row__icon">mdi-refresh</v-icon>
              <span class="sp-menu-row__label">{{
                tm("spcodeProjectLoad.indicator.manageProject")
              }}</span>
            </button>
          </template>

          <template v-else>
            <!-- ── 项目 ── -->
            <div class="sp-menu-title">
              {{ tm("spcodeProjectLoad.indicator.projectMenuTitle") }}
            </div>
            <div v-if="status.loaded" class="sp-menu-static">
              <v-icon size="14" class="sp-menu-row__icon">
                mdi-folder-check-outline
              </v-icon>
              <span class="sp-menu-row__label">{{ displayPath }}</span>
              <span
                v-if="status.directory"
                class="sp-menu-row__sub"
                :title="status.directory"
                >{{ status.directory }}</span
              >
            </div>
            <button type="button" class="sp-menu-row" @click="handleManageProject">
              <v-icon size="14" class="sp-menu-row__icon">
                {{ status.loaded ? "mdi-folder-sync-outline" : "mdi-folder-plus-outline" }}
              </v-icon>
              <span class="sp-menu-row__label">{{
                status.loaded
                  ? tm("spcodeProjectLoad.indicator.manageProject")
                  : tm("spcodeProjectLoad.indicator.loadProject")
              }}</span>
            </button>

            <!-- ── LLM 工作树（仅已加载项目时） ── -->
            <template v-if="status.loaded">
              <div class="sp-menu-divider"></div>
              <div class="sp-menu-title">
                {{ tm("spcodeProjectLoad.indicator.worktreeMenuTitle") }}
              </div>
              <div class="sp-wt-hint">
                {{ tm("spcodeProjectLoad.indicator.worktreeMenuHint") }}
              </div>
              <!-- "Not specified" option: clears the activation so the LLM
                   follows the project path guidance (default behavior). -->
              <button
                type="button"
                class="sp-wt-row"
                :class="{ 'sp-wt-row--selected': !activeWorktree }"
                :disabled="isSelectingWorktree"
                @click="selectWorktree(null)"
              >
                <v-icon size="14" class="sp-wt-row__icon">
                  mdi-folder-outline
                </v-icon>
                <span class="sp-wt-row__label">{{
                  tm("spcodeProjectLoad.indicator.worktreeNone")
                }}</span>
                <v-icon
                  v-if="!activeWorktree"
                  size="14"
                  class="sp-wt-row__check"
                >
                  mdi-check
                </v-icon>
              </button>
              <div v-if="worktreesLoading" class="sp-wt-hint">
                {{ tm("spcodeProjectLoad.indicator.worktreeLoading") }}
              </div>
              <template v-else-if="worktreeList.length">
                <button
                  v-for="wt in worktreeList"
                  :key="wt.path"
                  type="button"
                  class="sp-wt-row"
                  :class="{
                    'sp-wt-row--selected': activeWorktree === wt.path,
                  }"
                  :title="wt.path"
                  :disabled="isSelectingWorktree"
                  @click="selectWorktree(wt.path)"
                >
                  <v-icon size="14" class="sp-wt-row__icon">{{
                    wt.isMain
                      ? "mdi-home"
                      : wt.locked
                        ? "mdi-lock"
                        : "mdi-source-branch"
                  }}</v-icon>
                  <span class="sp-wt-row__label">{{ worktreeLabel(wt) }}</span>
                  <span v-if="wt.isMain" class="sp-wt-row__badge">{{
                    tm("spcodeProjectLoad.indicator.worktreeMainBadge")
                  }}</span>
                  <span v-else-if="!wt.branch" class="sp-wt-row__badge">{{
                    tm("spcodeProjectLoad.indicator.worktreeDetachedBadge")
                  }}</span>
                  <v-icon
                    v-if="activeWorktree === wt.path"
                    size="14"
                    class="sp-wt-row__check"
                  >
                    mdi-check
                  </v-icon>
                </button>
              </template>
              <div
                v-else-if="worktreesFailed"
                class="sp-wt-hint sp-wt-hint--error"
              >
                {{ tm("spcodeProjectLoad.indicator.worktreeLoadFailed") }}
              </div>
              <div v-else class="sp-wt-hint">
                {{ tm("spcodeProjectLoad.indicator.worktreeEmpty") }}
              </div>
            </template>

            <!-- ── 服务 ── -->
            <div class="sp-menu-divider"></div>
            <div class="sp-menu-title">
              {{ tm("spcodeProjectLoad.indicator.servicesTitle") }}
            </div>
            <!-- Codegraph -->
            <div class="sp-svc-row">
              <span
                class="sp-svc-row__dot"
                :class="`sp-svc-row__dot--${codegraphState.dot}`"
                aria-hidden="true"
              />
              <v-icon size="14" class="sp-svc-row__icon">
                {{ codegraphState.icon }}
              </v-icon>
              <span class="sp-svc-row__label">{{ codegraphState.label }}</span>
              <button
                type="button"
                class="sp-svc-row__action"
                @click="openCodegraphManager"
              >
                {{ tm("spcodeProjectLoad.indicator.manageCodegraph") }}
              </button>
            </div>
            <div
              class="sp-svc-row__detail"
              :title="`${tm('spcodeProjectLoad.indicator.defaultProjectPrefix')}: ${codegraphState.detail}`"
            >
              {{ tm("spcodeProjectLoad.indicator.defaultProjectPrefix") }}:
              {{ codegraphState.detail }}
            </div>
            <!-- Vivado -->
            <div class="sp-svc-row">
              <span
                class="sp-svc-row__dot"
                :class="`sp-svc-row__dot--${vivadoState.dot}`"
                aria-hidden="true"
              />
              <v-icon size="14" class="sp-svc-row__icon">
                {{ vivadoState.icon }}
              </v-icon>
              <span class="sp-svc-row__label">{{ vivadoState.label }}</span>
            </div>
            <div class="sp-svc-row__detail" :title="vivadoState.detail">
              {{ vivadoState.detail }}
            </div>
            <!-- Agent Memory（tc_memory 插件） -->
            <div class="sp-svc-row">
              <span
                class="sp-svc-row__dot"
                :class="`sp-svc-row__dot--${tcMemoryState.dot}`"
                aria-hidden="true"
              />
              <v-icon size="14" class="sp-svc-row__icon">
                {{ tcMemoryState.icon }}
              </v-icon>
              <span class="sp-svc-row__label">{{ tcMemoryState.label }}</span>
            </div>
            <div class="sp-svc-row__detail" :title="tcMemoryState.detail">
              {{ tcMemoryState.detail }}
            </div>
          </template>
        </v-card-text>
      </v-card>
    </v-menu>

    <!--
      Comic-style status bubble (2026-08-15): pops next to the capsule
      when codegraph state changes (initializing / restarting /
      connected / disconnected). Auto-hides after 3 s; a state update
      while visible resets the timer.
    -->
    <Transition name="sp-bubble">
      <div
        v-if="bubbleVisible"
        class="sp-bubble"
        role="status"
        :aria-label="bubbleText"
      >
        <span class="sp-bubble__text">{{ bubbleText }}</span>
        <button
          type="button"
          class="sp-bubble__close"
          :aria-label="tm('spcodeProjectLoad.indicator.dismissBubble')"
          @click="closeBubble"
        >
          <v-icon size="12">mdi-close</v-icon>
        </button>
        <span class="sp-bubble__tail" aria-hidden="true" />
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.sp-chip-wrap {
  display: inline-flex;
  align-items: center;
  min-width: 0;
  position: relative; /* anchor for the status bubble */
}

/* ── Ghost capsule (2026-10-03) ──
   Resting state carries a faint 1px border so the affordance reads as a
   clickable control (status row sits alone above the composer, so it may
   not rely on the toolbar's ghost vocabulary); hover/open strengthen the
   border and reveal a wash. The status dot and the green worktree suffix
   carry the semantics. */
.sp-capsule {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  box-sizing: border-box;
  height: 28px;
  padding: 0 9px;
  border: 1px solid var(--sp-chip-border);
  border-radius: 8px;
  background: transparent;
  color: var(--sp-text-muted);
  font-size: 12.5px;
  font-weight: 500;
  cursor: pointer;
  transition:
    background-color 150ms ease,
    border-color 150ms ease,
    color 150ms ease;
  max-width: min(320px, 100%);
  min-width: 0;
}

.sp-capsule:hover {
  background: var(--sp-ghost-hover-bg);
  border-color: var(--sp-chip-border-strong);
  color: var(--sp-text-primary);
}

.sp-capsule--open {
  background: var(--sp-ghost-open-bg);
  border-color: var(--sp-chip-border-strong);
  color: var(--sp-text-primary);
}

.sp-capsule:disabled {
  cursor: default;
}

.sp-capsule:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: 1px;
}

.sp-capsule__dot {
  flex: 0 0 6px;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--sp-status-dot-neutral);
  transition: background-color 200ms ease;
}

.sp-capsule__dot--success {
  background: var(--sp-status-dot-success);
}

.sp-capsule__dot--warning {
  background: var(--sp-status-dot-warning);
}

.sp-capsule__dot--hollow {
  background: transparent;
  box-shadow: inset 0 0 0 1.5px var(--sp-status-dot-neutral);
}

.sp-capsule__icon {
  flex: 0 0 14px;
  color: rgb(var(--v-theme-primary));
}

.sp-capsule--failed,
.sp-capsule--failed .sp-capsule__icon {
  color: rgb(var(--v-theme-error));
}

.sp-capsule .mdi-loading {
  animation: sp-rotate 1s linear infinite;
}

@keyframes sp-rotate {
  to {
    transform: rotate(360deg);
  }
}

.sp-capsule__label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sp-capsule__wt-sep {
  flex: 0 0 auto;
  color: var(--sp-status-dot-neutral);
}

.sp-capsule__wt-icon {
  flex: 0 0 12px;
  /* Worktree branch renders in the theme's primary blue (2026-10-03) so
     it reads as a contextual hint rather than a success state; the green
     status dot keeps the loaded/success semantics. */
  color: rgb(var(--v-theme-primary));
}

.sp-capsule__wt {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: rgb(var(--v-theme-primary));
}

.sp-capsule__chevron {
  flex: 0 0 auto;
  opacity: 0.5;
}

/* ── Unified dropdown sections ── */
.sp-menu-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--sp-text-path);
  margin: 8px 0 2px;
}

.sp-menu-title:first-child {
  margin-top: 0;
}

.sp-menu-divider {
  height: 1px;
  margin: 6px 2px;
  background: var(--sp-chip-divider);
}

.sp-menu-row,
.sp-menu-static {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 5px 6px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--sp-text-primary);
  font-size: 12.5px;
  text-align: left;
}

.sp-menu-row {
  cursor: pointer;
}

.sp-menu-row:hover {
  background: var(--sp-ghost-hover-bg);
}

.sp-menu-row__icon {
  flex: 0 0 14px;
  opacity: 0.7;
}

.sp-menu-row__label {
  min-width: 0;
  flex-shrink: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 500;
}

.sp-menu-row__sub {
  margin-left: auto;
  flex-shrink: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 11px;
  color: var(--sp-text-path);
  direction: ltr;
}

.sp-chip-popover-messages {
  margin: 0;
  white-space: pre-wrap;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  line-height: 1.5;
  max-height: 240px;
  overflow-y: auto;
}

/* ── Worktree rows ── */
.sp-wt-row {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  margin-top: 4px;
  padding: 4px 6px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--sp-text-primary);
  font-size: 12px;
  text-align: left;
  cursor: pointer;
}

.sp-wt-row:hover:not(:disabled) {
  background: var(--sp-ghost-hover-bg);
}

.sp-wt-row:disabled {
  opacity: 0.55;
  cursor: default;
}

.sp-wt-row__icon {
  flex: 0 0 14px;
  color: rgb(var(--v-theme-primary));
}

.sp-wt-row__label {
  min-width: 0;
  flex-shrink: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 500;
}

.sp-wt-row__badge {
  flex-shrink: 0;
  font-size: 10px;
  padding: 0 4px;
  border-radius: 999px;
  background: rgba(var(--v-theme-on-surface), 0.08);
  color: var(--sp-text-path);
}

.sp-wt-row__check {
  margin-left: auto;
  flex-shrink: 0;
  color: rgb(var(--v-theme-success));
}

.sp-wt-hint {
  margin-top: 6px;
  font-size: 11px;
  color: var(--sp-text-path);
}

.sp-wt-hint--error {
  color: rgb(var(--v-theme-error));
}

/* ── Services section ── */
.sp-svc-row {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 8px;
}

.sp-svc-row__dot {
  flex: 0 0 6px;
  width: 6px;
  height: 6px;
  border-radius: 50%;
}

.sp-svc-row__dot--success {
  background: var(--sp-status-dot-success);
}
.sp-svc-row__dot--warning {
  background: var(--sp-status-dot-warning);
}
.sp-svc-row__dot--error {
  background: var(--sp-status-dot-error);
}
.sp-svc-row__dot--neutral {
  background: var(--sp-status-dot-neutral);
}

.sp-svc-row__icon {
  flex: 0 0 14px;
  color: rgb(var(--v-theme-primary));
}

.sp-svc-row__label {
  font-size: 12px;
  font-weight: 500;
  white-space: nowrap;
  flex-shrink: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.sp-svc-row__action {
  margin-left: auto;
  border: 0;
  background: transparent;
  color: rgb(var(--v-theme-primary));
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  padding: 2px 6px;
  border-radius: 6px;
  flex-shrink: 0;
}

.sp-svc-row__action:hover {
  background: var(--sp-ghost-hover-bg);
}

.sp-svc-row__detail {
  margin: 2px 0 0 20px;
  font-size: 11px;
  color: var(--sp-text-path);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
}

/* ── Status bubble (2026-08-15) ── */
.sp-bubble {
  position: absolute;
  /* Anchor to the capsule's left edge. (elecvoid243, 2026-10-02) */
  left: 0;
  bottom: calc(100% + 10px);
  z-index: 30;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  max-width: 280px;
  padding: 6px 6px 6px 12px;
  font-size: 12px;
  font-weight: 500;
  color: var(--sp-text-primary);
  background: var(--sp-chip-bg);
  border: 1px solid var(--sp-chip-border);
  border-radius: 12px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.18);
}

.sp-bubble__text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

.sp-bubble__close {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  padding: 0;
  border: 0;
  border-radius: 50%;
  background: transparent;
  color: var(--sp-text-path);
  cursor: pointer;
}

.sp-bubble__close:hover {
  background: var(--sp-ghost-hover-bg);
  color: var(--sp-text-primary);
}

.sp-bubble__tail {
  position: absolute;
  top: 100%;
  left: 16px;
  width: 0;
  height: 0;
  border-left: 6px solid transparent;
  border-right: 6px solid transparent;
  border-top: 8px solid var(--sp-chip-bg);
  filter: drop-shadow(0 1px 0 var(--sp-chip-border));
}

.sp-bubble-enter-active,
.sp-bubble-leave-active {
  transition:
    opacity 150ms ease,
    transform 150ms ease;
}

.sp-bubble-enter-from,
.sp-bubble-leave-to {
  opacity: 0;
  transform: translateY(4px);
}
</style>
