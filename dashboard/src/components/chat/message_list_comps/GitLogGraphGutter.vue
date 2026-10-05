<script setup lang="ts">
// Author: elecvoid243 @ 2026-10-05
// Spec: docs/superpowers/specs/2026-10-05-git-log-graph-and-branch-tree-design.md §4.2
//
// 一行的 lane gutter。竖线用绝对定位元素（top/bottom）而不是 SVG path ——
// 行展开出 diff / commit body 时它们随行高自然拉伸，不必重算 lane，也不会
// 把曲线拉变形。曲线与节点留在固定高度的 SVG 层里。

import { computed } from "vue";
import {
  BAND,
  GUTTER_LEFT,
  GUTTER_PAD,
  LANE_W,
  NODE_CY,
  STROKE_W,
  gutterWidth,
  type GraphRow,
} from "@/composables/gitGraphLayout";

// 定位写在行内:gutter 的孩子全是绝对定位元素,留在流内它的高度会塌成 0,
// top:0;bottom:0 的竖线随之高度 0 —— 真机上只剩孤立的圆点。行内声明也让
// jsdom 能守住这条契约(模板根层不要放注释,否则组件变多根)。
const props = defineProps<{
  row: GraphRow;
  lanes: number;
  /** 该行是 HEAD：画环形节点（衬底 + 外环 + 内点），避免竖线从环心漏出。 */
  isHead?: boolean;
}>();

const x = (lane: number): number => GUTTER_PAD + lane * LANE_W + LANE_W / 2;
const laneColor = (lane: number): string => `var(--spcode-graph-l${lane % 6})`;

const width = computed(() => gutterWidth(props.lanes));
/** 曲线止于曲线层底(BAND):目标列若本行没有贯穿竖线,要补一段接到行底。 */
const outContinuations = computed(() =>
  props.row.outs.filter((l) => !props.row.passIn.includes(l)),
);
const nodeX = computed(() => x(props.row.lane));

/** 收敛：col@行顶 → 节点@NODE_CY。 */
const inPath = (from: number): string =>
  `M ${x(from)} 0 C ${x(from)} ${NODE_CY * 0.55} ${nodeX.value} ${
    NODE_CY * 0.45
  } ${nodeX.value} ${NODE_CY}`;

/** 分叉 / 汇出：节点@NODE_CY → col@BAND（正好接上下一行的行顶）。 */
const outPath = (to: number): string =>
  `M ${nodeX.value} ${NODE_CY} C ${nodeX.value} ${NODE_CY + 9} ${
    x(to)
  } ${BAND - 9} ${x(to)} ${BAND}`;
</script>

<template>
  <div
    class="git-log-gutter"
    :style="{
      position: 'absolute',
      left: GUTTER_LEFT + 'px',
      top: '0px',
      // 下沿多出 1px:跨过 .git-log-item 的 border-bottom,否则每条 lane
      // 在每个行边界都缺 1px。
      bottom: '-1px',
      width: width + 'px',
      '--spcode-graph-sw': STROKE_W + 'px',
    }"
  >
    <span
      v-for="l in row.passIn"
      :key="`pass-${l}`"
      class="git-log-gutter-v"
      data-seg="pass"
      :data-lane="l"
      :style="{ left: x(l) + 'px', background: laneColor(l) }"
    />
    <span
      class="git-log-gutter-v"
      data-seg="node-up"
      :data-lane="row.lane"
      :style="{
        left: nodeX + 'px',
        height: NODE_CY + 'px',
        background: laneColor(row.lane),
      }"
    />
    <span
      v-if="row.cont"
      class="git-log-gutter-v"
      data-seg="node-down"
      :data-dangling="String(row.dangling)"
      :data-lane="row.lane"
      :style="{
        left: nodeX + 'px',
        top: NODE_CY + 'px',
        background: laneColor(row.lane),
      }"
    />

    <!-- 弧线以下到行底的这一小段:不补它,分叉处就会留 24~26px 断口 -->
    <span
      v-for="l in outContinuations"
      :key="`out-${l}`"
      class="git-log-gutter-v"
      data-seg="out"
      :data-lane="l"
      :style="{ left: x(l) + 'px', top: BAND + 'px', background: laneColor(l) }"
    />
    <svg
      class="git-log-gutter-svg"
      :width="width"
      :height="BAND"
      aria-hidden="true"
    >
      <path
        v-for="l in row.ins"
        :key="`in-${l}`"
        :d="inPath(l)"
        :stroke="laneColor(l)"
        :stroke-width="STROKE_W"
      />
      <path
        v-for="l in row.outs"
        :key="`out-${l}`"
        :d="outPath(l)"
        :stroke="laneColor(l)"
        :stroke-width="STROKE_W"
      />
      <template v-if="isHead">
        <circle :cx="nodeX" :cy="NODE_CY" r="5.3" class="git-log-gutter-ring-bg" />
        <circle
          :cx="nodeX"
          :cy="NODE_CY"
          r="5.3"
          fill="none"
          :stroke="laneColor(row.lane)"
          stroke-width="1.1"
        />
        <circle :cx="nodeX" :cy="NODE_CY" r="2.9" :fill="laneColor(row.lane)" />
      </template>
      <circle
        v-else
        :cx="nodeX"
        :cy="NODE_CY"
        r="3.3"
        :fill="laneColor(row.lane)"
      />
    </svg>
  </div>
</template>

<style scoped>
/* lane 颜色是装饰性的（颜色 = 列，列可复用），分支身份由 ref chip 的文本
   承载 —— 主题切换只换色值，不换信息。默认给浅色底用的深一档色值。 */
.git-log-gutter {
  --spcode-graph-l0: #4a6fd8;
  --spcode-graph-l1: #2b8f86;
  --spcode-graph-l2: #a97a1f;
  --spcode-graph-l3: #b4595c;
  --spcode-graph-l4: #6f5fb0;
  --spcode-graph-l5: #5f8a46;
}

.v-theme--dark .git-log-gutter {
  --spcode-graph-l0: #7f9cf5;
  --spcode-graph-l1: #4db6ac;
  --spcode-graph-l2: #d4a15a;
  --spcode-graph-l3: #d98c8c;
  --spcode-graph-l4: #9e8fd0;
  --spcode-graph-l5: #8fb877;
}

.git-log-gutter-v {
  position: absolute;
  top: 0;
  bottom: 0;
  width: var(--spcode-graph-sw, 1.5px);
  border-radius: 1px;
}

/* 上段只有固定高度（内联），不能拉到行底 */
.git-log-gutter-v[data-seg="node-up"] {
  bottom: auto;
}

/* 父提交在窗口外（浅克隆 / 1MB 截断）：淡出，而不是伪造一条通往别处的边 */
.git-log-gutter-v[data-dangling="true"] {
  opacity: 0.32;
}

.git-log-gutter-svg {
  position: absolute;
  top: 0;
  left: 0;
  overflow: visible;
}

.git-log-gutter-svg path {
  fill: none;
  stroke-width: 1.5;
  stroke-linecap: round;
}

/* HEAD 环的衬底：盖住贯穿竖线，否则线会从环心穿过去 */
.git-log-gutter-ring-bg {
  fill: rgb(var(--v-theme-surface));
}
</style>
