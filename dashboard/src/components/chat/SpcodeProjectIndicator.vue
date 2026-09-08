<!--
  Author: elecvoid243, 2026-07-09
  Spec: docs/superpowers/specs/2026-07-09-chat-input-chips-beautify-design.md §5.1, §5.2

  SpcodeProjectIndicator — status badge for the loaded/unloaded spcode project.

  Visual states (locked by spec §5.2):
    - Not loaded → empty state (empty dot ring + mdi-folder-outline + "未加载项目")
    - Loaded → success dot + mdi-folder-check-outline + "项目已加载" + truncated path

  Event contract (unchanged from prior version):
    - Emits `open-load-dialog` on click (suppressed while a silent
      operation is running)

  Progress states (2026-08-06, driven by useSpcodeOperationProgress):
    - project_load / project_unload running → spinning mdi-loading + currentStep
    - project_load / project_unload failed  → red error icon + chevron popover
      with the full substep log; click still opens the dialog for retry
-->
<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useModuleI18n } from "@/i18n/composables";
import { useSpcodeProjectStatus } from "@/composables/useSpcodeProjectStatus";
import { useSpcodeOperationProgress } from "@/composables/useSpcodeOperationProgress";
import { useSpcodeCodegraphStatus } from "@/composables/useSpcodeCodegraphStatus";
import { useSpcodeVivadoStatus } from "@/composables/useSpcodeVivadoStatus";
import { useSpcodeWorktrees } from "@/composables/useSpcodeWorktrees";
import type { SpcodeGitWorktree } from "@/composables/parseSpcodeWorktrees";

const { status } = useSpcodeProjectStatus();
const { tm } = useModuleI18n("features/chat");
const { progress } = useSpcodeOperationProgress();
const popoverOpen = ref(false);

const emit = defineEmits<{
  (e: "open-load-dialog"): void;
  (e: "open-codegraph-dialog"): void;
}>();

// Only project load/unload operations drive THIS chip. codegraph_set
// progress had a dedicated badge on the removed SpcodeCodegraphChip; the
// services popover now reflects codegraph state reactively instead.
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
 * Show only the basename of a loaded path so the chip stays compact;
 * the full path is available via the hover tooltip.
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
  // current_step(如"⏳ [2/3] codegraph init")——细节交给 codegraph
  // 状态气泡;加载失败时 yield 信息仍可在失败 popover 中查看。
  if (isLoading.value) {
    return tm("spcodeProjectLoad.indicator.loading");
  }
  if (isFailed.value) return tm("spcodeProjectLoad.indicator.failed");
  return status.value.loaded
    ? tm("spcodeProjectLoad.indicator.loadedLabel")
    : tm("spcodeProjectLoad.indicator.noProject");
});

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
    return parts.join(" · ");
  }
  return tm("spcodeProjectLoad.indicator.noProject");
});

// ── 服务状态 popover (2026-08-15) ─────────────────────────────────────
// 原 SpcodeCodegraphChip / SpcodeVivadoStatusChip 移除后,两个 MCP 服务的
// 状态查看整合到 project chip 旁的小按钮 popover 中。数据源仍是同一对
// 模块级单例 composable(useSpcodeCodegraphStatus / useSpcodeVivadoStatus),
// ChatInput 的轮询/前台刷新逻辑继续驱动它们。
const servicesMenuOpen = ref(false);

const codegraph = useSpcodeCodegraphStatus();
const vivado = useSpcodeVivadoStatus();

// codegraph 状态只保留 2 态(mcpRunning 单一维度)。
// 2026-09-08: MCP 在跑即视为"已加载"——不再把"未设置默认项目"当作未加载
// 态(system_prompt 已要求每次 codegraph_explore 显式传 projectPath,默认
// 目录缺失不影响 codegraph 可用性);默认目录改在 detail 行提示。
// 旧 SpCodegraphChip 的"路径不匹配"提醒已于 2026-08-15 随 chip 移除。
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

/**
 * Codegraph 管理入口:关闭 popover 并委托给 ChatInput 打开
 * ``ProjectLoadDialog command-mode="codegraph"``(原 codegraph chip 的 click 行为)。
 */
function openCodegraphManager(): void {
  servicesMenuOpen.value = false;
  emit("open-codegraph-dialog");
}

// ── Worktree 激活 (2026-08-20) ────────────────────────────────────────
// GitDiffSidebar 回归纯项目操作;worktree 激活(指定 LLM 工作在哪个
// worktree,后端以 extra_user_content_parts 注入每次 LLM 请求)的入口
// 放在 project chip 旁的小按钮(与"查看服务状态"按钮同款几何,不叠加
// ——chip 宽度随项目名变化,叠加定位会在长名截断时脱离 chip)。
// 数据与操作复用 useSpcodeWorktrees(GET /spcode/git-worktrees +
// POST /spcode/worktree-activate)。
const worktrees = useSpcodeWorktrees();
const worktreeMenuOpen = ref(false);
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

// 打开菜单时刷新列表(worktree 增删多发生在 GitDiffSidebar/外部,这里不轮询;
// 项目加载/切换由 useSpcodeWorktrees 内部的 umo/directory watcher 自动刷新)。
watch(worktreeMenuOpen, (open) => {
  if (open) void worktrees.refresh();
});

function worktreeLabel(wt: SpcodeGitWorktree): string {
  return wt.branch ?? (wt.isMain
    ? tm("spcodeProjectLoad.indicator.worktreeMainBadge")
    : wt.headSha.slice(0, 7));
}

const worktreeBtnTooltip = computed(() =>
  activeWorktree.value
    ? tm("spcodeProjectLoad.indicator.worktreeBtnTooltipActive", {
        branch: worktreeList.value.find((w) => w.path === activeWorktree.value)
          ? worktreeLabel(
              worktreeList.value.find((w) => w.path === activeWorktree.value)!,
            )
          : (activeWorktree.value ?? ""),
      })
    : tm("spcodeProjectLoad.indicator.worktreeBtnTooltip"),
);

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
    worktreeMenuOpen.value = false;
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
// codegraph 状态变更(或 codegraph 相关操作进行中)时,于 services 按钮旁
// 弹一个漫画式气泡实时提示,3s 后消失;显示期间状态再次更新则重置计时。
// 气泡右上角提供 ✕ 按钮可立即关闭。
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
 * 优先级: 进行中的 codegraph 相关操作 > MCP 状态转变 > 运行中切换项目。
 */
function deriveBubbleMessage(now: BubbleSnapshot, prev: BubbleSnapshot): string | null {
  // 1) 进行中操作(最高优先): project_load 含 codegraph 步骤 / codegraph_set
  if (now.st === "running") {
    if (now.op === "codegraph_set") {
      return tm("spcodeProjectLoad.indicator.codegraphRestarting");
    }
    if (now.op === "codegraph_init") {
      return tm("spcodeProjectLoad.indicator.codegraphIndexing");
    }
    if (now.op === "project_load" && /codegraph/i.test(now.step)) {
      return tm("spcodeProjectLoad.indicator.codegraphInitializing");
    }
  }
  // 2) MCP 状态转变
  if (now.mcp && !prev.mcp) {
    return tm("spcodeProjectLoad.indicator.codegraphConnected");
  }
  if (!now.mcp && prev.mcp) {
    // 运行→停止。操作进行中已由 1 覆盖(显示"初始化/重启中"),
    // 走到这里说明是真正被关闭/掉线。
    return tm("spcodeProjectLoad.indicator.codegraphDisconnected");
  }
  // 3) 运行中切换默认项目(codegraph set 完成后 mcp 保持 true,靠 proj 变化感知)
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
</script>

<template>
  <div class="sp-chip-wrap">
    <v-tooltip location="bottom" :open-delay="200">
      <template #activator="{ props: tipProps }">
        <button
          v-bind="tipProps"
          type="button"
          :class="[
            'sp-status-badge',
            {
              'sp-status-badge--empty': !status.loaded && !isLoading && !isFailed,
              'sp-status-badge--failed': isFailed,
            },
          ]"
          :aria-label="tooltipText"
          @click="openLoadDialog"
        >
          <span
            class="sp-status-badge__dot"
            :class="{
              'sp-status-badge__dot--success': status.loaded && !isLoading && !isFailed,
              'sp-status-badge__dot--warning': isFailed,
              'sp-status-badge__dot--neutral': !status.loaded && !isLoading && !isFailed,
            }"
            aria-hidden="true"
          />
          <v-icon size="14" class="sp-status-badge__icon">{{ icon }}</v-icon>
          <span class="sp-status-badge__label">{{ label }}</span>
          <span
            v-if="displayPath && !isLoading && !isFailed"
            class="sp-status-badge__path"
            >{{ displayPath }}</span
          >
        </button>
      </template>
      <span>{{ tooltipText }}</span>
    </v-tooltip>

    <!--
      Worktree activation side button (2026-08-20): a small button right
      next to the chip, sharing the services side button's geometry
      (shown only when a project is loaded). Deliberately NOT overlaid on
      the chip — the chip's width varies with the project name, which
      left an absolutely positioned overlay detached from it. The
      popover lets the user pick which worktree the LLM should work in —
      the backend injects the activated worktree into every LLM request
      via extra_user_content_parts. Kept here rather than in
      GitDiffSidebar because it is prompt-injection guidance, not a
      direct project operation.
    -->
    <v-menu
      v-if="status.loaded"
      v-model="worktreeMenuOpen"
      location="bottom start"
      transition="none"
    >
      <template #activator="{ props: menuProps }">
        <v-tooltip location="bottom" :open-delay="200">
          <template #activator="{ props: tipProps }">
            <button
              v-bind="{ ...tipProps, ...menuProps }"
              type="button"
              class="sp-chip-services-btn sp-chip-wt-btn"
              :class="{ 'sp-chip-wt-btn--active': !!activeWorktree }"
              :aria-label="tm('spcodeProjectLoad.indicator.worktreeBtnTooltip')"
            >
              <v-icon size="14">{{
                activeWorktree ? "mdi-check-bold" : "mdi-source-branch"
              }}</v-icon>
            </button>
          </template>
          <span>{{ worktreeBtnTooltip }}</span>
        </v-tooltip>
      </template>
      <v-card min-width="300" max-width="420">
        <v-card-text>
          <div class="sp-chip-popover-title">
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
            <v-icon v-if="!activeWorktree" size="14" class="sp-wt-row__check">
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
              :class="{ 'sp-wt-row--selected': activeWorktree === wt.path }"
              :title="wt.path"
              :disabled="isSelectingWorktree"
              @click="selectWorktree(wt.path)"
            >
              <v-icon size="14" class="sp-wt-row__icon">{{
                wt.isMain ? "mdi-home" : wt.locked ? "mdi-lock" : "mdi-source-branch"
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
          <div v-else-if="worktreesFailed" class="sp-wt-hint sp-wt-hint--error">
            {{ tm("spcodeProjectLoad.indicator.worktreeLoadFailed") }}
          </div>
          <div v-else class="sp-wt-hint">
            {{ tm("spcodeProjectLoad.indicator.worktreeEmpty") }}
          </div>
        </v-card-text>
      </v-card>
    </v-menu>

    <v-menu
      v-if="isFailed"
      v-model="popoverOpen"
      location="bottom start"
      transition="none"
    >
      <template #activator="{ props: menuProps }">
        <button
          v-bind="menuProps"
          class="sp-chip-details-btn"
          type="button"
          :aria-label="tm('spcodeProjectLoad.indicator.failedDetailTitle')"
          @click.stop
        >
          <v-icon size="14">mdi-chevron-down</v-icon>
        </button>
      </template>
      <v-card min-width="320" max-width="480">
        <v-card-text>
          <div class="sp-chip-popover-title">
            {{ tm("spcodeProjectLoad.indicator.failedDetailTitle") }}
          </div>
          <pre class="sp-chip-popover-messages">{{ progress.messages.join("\n") }}</pre>
        </v-card-text>
      </v-card>
    </v-menu>

    <!--
      Services status popover (2026-08-15): the codegraph + vivado status
      chips were removed from the input row; their status is now reachable
      through this small button next to the project chip. The popover shows
      both MCP services' state, plus a manage entry that re-opens the
      codegraph load dialog (same one the old chip opened).
    -->
    <v-menu
      v-model="servicesMenuOpen"
      location="bottom start"
      transition="none"
    >
      <template #activator="{ props: menuProps }">
        <v-tooltip location="bottom" :open-delay="200">
          <template #activator="{ props: tipProps }">
            <button
              v-bind="{ ...tipProps, ...menuProps }"
              type="button"
              class="sp-chip-services-btn"
              :aria-label="tm('spcodeProjectLoad.indicator.servicesTooltip')"
            >
              <v-icon size="14">mdi-server-network</v-icon>
            </button>
          </template>
          <span>{{ tm("spcodeProjectLoad.indicator.servicesTooltip") }}</span>
        </v-tooltip>
      </template>
      <v-card min-width="320" max-width="420">
        <v-card-text>
          <div class="sp-chip-popover-title">
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
        </v-card-text>
      </v-card>
    </v-menu>

    <!--
      Comic-style status bubble (2026-08-15): pops next to the services
      button when codegraph state changes (initializing / restarting /
      connected / disconnected). Auto-hides after 5 s; a state update
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
.sp-status-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: var(--sp-chip-height);
  padding: 0 10px;
  border: 1px solid var(--sp-chip-border);
  border-radius: 12px;
  background: var(--sp-chip-bg);
  color: var(--sp-text-primary);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: background-color 150ms ease;
  max-width: min(240px, 100%);
  min-width: 0;
}

.sp-status-badge:hover {
  background: var(--sp-chip-hover-bg);
}
.sp-status-badge:active {
  background: var(--sp-chip-active-bg);
}
.sp-status-badge:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: 1px;
}

.sp-status-badge__dot {
  flex: 0 0 6px;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--sp-status-dot-success);
  transition: background-color 200ms ease;
}

.sp-status-badge__dot--neutral {
  background: var(--sp-status-dot-neutral);
}

.sp-status-badge--empty .sp-status-badge__dot {
  background: transparent;
  box-shadow: inset 0 0 0 1.5px var(--sp-status-dot-neutral);
}

.sp-status-badge__icon {
  flex: 0 0 14px;
  color: rgb(var(--v-theme-primary));
}

.sp-status-badge__label {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
  flex-shrink: 1;
}

.sp-status-badge__path {
  font-family: var(--v-font-mono, monospace);
  font-size: 11px;
  font-weight: 400;
  color: var(--sp-text-path);
  max-width: 12rem;
  min-width: 0;
  flex-shrink: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ── Silent-operation progress states (2026-08-06) ── */
.sp-chip-wrap {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  position: relative; /* anchor for the status bubble */
}

/* ── Worktree activation side button (2026-08-20) ──
   Sits right next to the chip, NOT overlaid — the chip's width varies
   with the project name (long names truncate to "xxx…"), which left an
   absolutely positioned overlay detached from the chip edge. Geometry
   comes from the shared .sp-chip-services-btn class; this only adds the
   activated accent (the LLM is pinned to that worktree). */
.sp-chip-wt-btn--active {
  color: rgb(var(--v-theme-success));
  border-color: rgba(var(--v-theme-success), 0.55);
}

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
  background: var(--sp-chip-hover-bg);
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

.sp-status-badge--failed {
  color: rgb(var(--v-theme-error));
}

.sp-status-badge--failed .sp-status-badge__icon {
  color: rgb(var(--v-theme-error));
}

.sp-status-badge .mdi-loading {
  animation: sp-rotate 1s linear infinite;
}

@keyframes sp-rotate {
  to {
    transform: rotate(360deg);
  }
}

.sp-chip-details-btn {
  border: 0;
  background: transparent;
  color: rgb(var(--v-theme-error));
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  padding: 0;
}

.sp-chip-popover-title {
  font-weight: 600;
  margin-bottom: 8px;
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

/* ── Services status popover (2026-08-15) ── */
.sp-chip-services-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: var(--sp-chip-height);
  height: var(--sp-chip-height);
  padding: 0;
  border: 1px solid var(--sp-chip-border);
  border-radius: 10px;
  background: var(--sp-chip-bg);
  color: var(--sp-text-primary);
  cursor: pointer;
  transition: background-color 150ms ease;
}

.sp-chip-services-btn:hover {
  background: var(--sp-chip-hover-bg);
}
.sp-chip-services-btn:active {
  background: var(--sp-chip-active-bg);
}
.sp-chip-services-btn:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: 1px;
}

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
  background: var(--sp-chip-hover-bg);
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
  right: 0;
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
  background: var(--sp-chip-hover-bg);
  color: var(--sp-text-primary);
}

.sp-bubble__tail {
  position: absolute;
  top: 100%;
  right: 16px;
  width: 0;
  height: 0;
  border-left: 6px solid transparent;
  border-right: 6px solid transparent;
  border-top: 8px solid var(--sp-chip-bg);
  filter: drop-shadow(0 1px 0 var(--sp-chip-border));
}

.sp-bubble-enter-active,
.sp-bubble-leave-active {
  transition: opacity 150ms ease, transform 150ms ease;
}

.sp-bubble-enter-from,
.sp-bubble-leave-to {
  opacity: 0;
  transform: translateY(4px);
}
</style>
