import { beforeEach, describe, expect, it, vi } from "vitest";

const getMock = vi.fn();
vi.mock("@/api/v1", () => ({
  pluginExtensionApi: { get: (...args: unknown[]) => getMock(...args) },
}));

// useSpcodeProjectStatus 的模块级 status 在测试环境初始为 EMPTY_STATUS
// (umo: null),会让 refresh() 在发请求前提前 return;这里固定返回一个
// 已加载的 umo,保证请求真正发出。
vi.mock("@/composables/useSpcodeProjectStatus", () => ({
  useSpcodeProjectStatus: () => ({
    status: { value: { umo: "u", directory: "d" } },
  }),
}));

import { useSpcodeGitLog } from "@/composables/useSpcodeGitLog";

function okResponse(commits: unknown[] = [], etag = 'W/"1"') {
  return {
    status: 200,
    data: {
      status: "ok",
      data: {
        // 成功响应必须带 reason: null —— parseSpcodeGitWorkflow 的
        // deriveSuccess 以 `reason === null` 判定成功;缺省时会被判为失败,
        // 导致 composable 提前进入 error 分支、不写入 ETag,第二个用例就
        // 无法真正覆盖 ETag 分桶行为。
        reason: null,
        loaded: true,
        umo: "u",
        worktree: "",
        directory: "d",
        ref: "HEAD",
        count: commits.length,
        has_more: false,
        truncated: false,
        max_bytes: 1024,
        commits,
      },
    },
    headers: { ETag: etag },
  };
}

describe("useSpcodeGitLog grep", () => {
  beforeEach(() => {
    getMock.mockReset();
    getMock.mockResolvedValue(okResponse());
    vi.useFakeTimers();
  });

  it("sends grep only when set", async () => {
    const log = useSpcodeGitLog();
    await log.refresh({ ref: "HEAD", n: 20, grep: "terminal" });
    expect(getMock.mock.calls[0][1].params.grep).toBe("terminal");

    await log.refresh({ ref: "HEAD", n: 20 });
    expect(getMock.mock.calls[1][1].params.grep).toBeUndefined();
  });

  it("uses a distinct ETag bucket per grep value", async () => {
    const log = useSpcodeGitLog();
    await log.refresh({ ref: "HEAD", n: 20, grep: "a" });
    const firstEtag = getMock.mock.results[0].value;
    expect(firstEtag).toBeDefined();
    await log.refresh({ ref: "HEAD", n: 20, grep: "b" });
    // 第二个请求不应携带第一个 grep 桶的 If-None-Match
    expect(getMock.mock.calls[1][1].headers["If-None-Match"]).toBeUndefined();
  });
});

// 2026-09-09 (elecvoid243) split-ref-filter: `rev`（SHA / 标签）与 `ref`
// （分支）拆开后，线上参数仍只有一个 ref —— rev 非空时覆盖分支，为空时
// 回落到分支；但 ETag 分桶必须把 rev 纳入，否则同一分支下的不同 SHA
// 检索会互相 304 复用快照。
describe("useSpcodeGitLog rev override", () => {
  beforeEach(() => {
    getMock.mockReset();
    getMock.mockResolvedValue(okResponse());
    vi.useFakeTimers();
  });

  it("sends rev as the wire ref while the branch stays in the filter", async () => {
    const log = useSpcodeGitLog();
    await log.refresh({ ref: "main", n: 20, rev: "abc1234" });
    expect(getMock.mock.calls[0][1].params.ref).toBe("abc1234");
    expect(log.filter.value.ref).toBe("main");
    expect(log.filter.value.rev).toBe("abc1234");
  });

  it("falls back to the branch when rev is empty or null", async () => {
    const log = useSpcodeGitLog();
    await log.refresh({ ref: "dev", n: 20, rev: "" });
    expect(getMock.mock.calls[0][1].params.ref).toBe("dev");

    await log.refresh({ ref: "dev", n: 20, rev: null });
    expect(getMock.mock.calls[1][1].params.ref).toBe("dev");
  });

  it("uses a distinct ETag bucket per rev under the same branch", async () => {
    const log = useSpcodeGitLog();
    await log.refresh({ ref: "main", n: 20, rev: "aaaaaaa" });
    await log.refresh({ ref: "main", n: 20, rev: "bbbbbbb" });
    expect(getMock.mock.calls[1][1].headers["If-None-Match"]).toBeUndefined();
  });
});
