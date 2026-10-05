// Author: elecvoid243 @ 2026-10-05
// Spec: docs/superpowers/specs/2026-10-05-git-log-graph-and-branch-tree-design.md §4.2 / §4.3
//
// 把 topo 序的 commit DAG 摊成「每行画什么」的纯数据：lane 只复用、不重排，
// 因为虚拟滚动时同一列的 x 坐标必须恒定，否则滚一屏整棵树横向抖动。
//
// 收敛语义（2026-10-05 裁定，见前端 ledger Task 2 的 Ruling）：第一父提交
// 已被别列期望时**不释放本列**，两列并行指向同一提交，等到该提交所在行再
// 由 `ins` 收敛 —— 与 gitk / GitHub 的 Y 形一致。

/** 单列宽（px）。改它会同时改 gutter 宽度与所有曲线端点。 */
export const LANE_W = 13;
/** gutter 左右内边距（px）。 */
export const GUTTER_PAD = 4;
/** gutter 距行左边界的偏移（px）—— 与 .git-log-item 的 padding-left 对齐。 */
export const GUTTER_LEFT = 12;
/** 节点圆心距行顶的距离（px）—— 与行内复选框同心（top:9px + 16px 盒）。 */
export const NODE_CY = 17;
/** 曲线 / 节点 SVG 层的高度（px），只覆盖 commit 行首行。 */
export const BAND = 34;

/**
 * gutter 宽度。单列（线性历史）返回 0 —— 零占用，窄侧边栏不该为一条竖线
 * 白掉 13px。
 */
export function gutterWidth(lanes: number): number {
  return lanes > 1 ? GUTTER_PAD * 2 + lanes * LANE_W : 0;
}

export interface GraphCommit {
  sha: string;
  parents: string[];
}

/** 一行的渲染指令；字段含义见 spec §4.3 的 5 步。 */
export interface GraphRow {
  /** 本行节点所在列。 */
  lane: number;
  /** 自上方收敛进本行节点的列（画 col@0 → lane@NODE_CY 曲线）。 */
  ins: number[];
  /** 贯穿整行的列（画满高竖线）。 */
  passIn: number[];
  /** 本行分叉 / 汇出到的列（画 lane@NODE_CY → col@BAND 曲线）。 */
  outs: number[];
  /** 节点所属列是否向下继续。 */
  cont: boolean;
  /** 继续、但目标提交不在当前窗口（浅克隆 / 1MB 截断）→ 淡出。 */
  dangling: boolean;
}

export interface GraphLayout {
  rows: GraphRow[];
  /** 用到的列数（= 最大列下标 + 1）。 */
  lanes: number;
}

/**
 * O(rows × lanes) 单趟布局。任何指向窗口外父提交的行都会得到
 * `dangling: true`，由渲染层画淡化短线，而不是伪造一条通向别处的边。
 */
export function layoutGraph(commits: GraphCommit[]): GraphLayout {
  const inWindow = new Set(commits.map((c) => c.sha));

  /** 每列期望的下一个 sha（null = 空闲）。 */
  const laneSha: (string | null)[] = [];
  /** 该列期望的 sha 是否在窗口外。 */
  const laneDangling: boolean[] = [];
  const rows: GraphRow[] = [];
  let lanes = 0;

  const allocate = (sha: string): number => {
    let l = laneSha.indexOf(null);
    if (l < 0) {
      laneSha.push(null);
      laneDangling.push(false);
      l = laneSha.length - 1;
    }
    laneSha[l] = sha;
    laneDangling[l] = !inWindow.has(sha);
    return l;
  };

  for (const commit of commits) {
    const before = laneSha.slice();

    let lane = laneSha.indexOf(commit.sha);
    if (lane < 0) lane = allocate(commit.sha);

    const ins: number[] = [];
    const passIn: number[] = [];
    for (let l = 0; l < before.length; l++) {
      const sha = before[l];
      if (!sha || l === lane) continue;
      if (sha === commit.sha) ins.push(l);
      else passIn.push(l);
    }
    // 收敛的列在本行终结 —— 必须在处理父提交之前释放，否则下面
    // allocate() 会白白新开一列而不是复用刚空出的那一列。
    for (const l of ins) {
      laneSha[l] = null;
      laneDangling[l] = false;
    }

    const outs: number[] = [];
    commit.parents.forEach((parent, k) => {
      if (k === 0) {
        laneSha[lane] = parent;
        laneDangling[lane] = !inWindow.has(parent);
        return;
      }
      const tgt = laneSha.indexOf(parent);
      if (tgt < 0) outs.push(allocate(parent));
      else if (tgt !== lane) outs.push(tgt);
    });
    if (commit.parents.length === 0) {
      laneSha[lane] = null;
      laneDangling[lane] = false;
    }

    const cont = laneSha[lane] != null;
    rows.push({
      lane,
      ins,
      passIn,
      outs,
      cont,
      dangling: cont && laneDangling[lane] === true,
    });

    lanes = Math.max(
      lanes,
      lane + 1,
      ...ins.map((l) => l + 1),
      ...passIn.map((l) => l + 1),
      ...outs.map((l) => l + 1),
    );
  }

  return { rows, lanes };
}
